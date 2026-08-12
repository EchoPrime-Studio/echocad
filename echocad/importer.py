# DXF를 CAD 레이어별 QGIS 벡터 레이어로 나누는 모듈. 원본 도면과 같은 색·글자로 보이게 스타일까지 옮긴다
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from osgeo import ogr
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsSingleSymbolRenderer,
    QgsSymbol,
    QgsSymbolLayer,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtXml import QDomDocument

from . import dxfenc, engine, linetypes, names, outliers, styles
from .gdalopts import dxf_options

try:
    from . import pro
    from .pro import attribs as pro_attribs
except ImportError:  # Community 빌드에는 pro/ 폴더가 없다
    pro = None
    pro_attribs = None

# 멀티파트로 통일한다 — DXF는 한 엔티티가 여러 파트인 경우가 흔하고, 단일파트 레이어에
# 넣으면 GeoPackage 규격에 어긋나 드라이버가 경고를 낸다.
_GEOMETRY = {
    QgsWkbTypes.PointGeometry: ("MultiPoint", "point"),
    QgsWkbTypes.LineGeometry: ("MultiLineString", "line"),
    QgsWkbTypes.PolygonGeometry: ("MultiPolygon", "polygon"),
}

_FIELDS = (
    "&field=cad_layer:string"
    "&field=text:string"
    "&field=color:string"
    "&field=fill_color:string"
    "&field=stroke_style:string"
    "&field=stroke_width:double"
    "&field=text_size:double"
    "&field=text_angle:double"
    "&field=text_quad:integer"
    "&field=text_dx:double"
    "&field=text_dy:double"
)


@dataclass
class LayerResult:
    name: str
    cad_layer: str
    geometry_type: str
    feature_count: int


@dataclass
class ImportResult:
    file: str
    status: str  # ok | unsupported | error | timeout | empty
    note: str = ""
    layers: list[LayerResult] = field(default_factory=list)
    total_features: int = 0
    dropped_features: int = 0  # 좌표가 깨져 제외한 피처 수
    unmatched_layers: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


def import_dwg(
    dwg: Path,
    output_gpkg: Path | None = None,
    crs: str | None = None,
    exe: Path | None = None,
    feedback=None,
    extract_attribs: bool = True,
    profile=None,
) -> tuple[ImportResult, list[QgsVectorLayer]]:
    """DWG 하나를 QGIS 레이어들로 바꾼다.

    output_gpkg가 없으면 메모리 레이어를 돌려준다. feedback은 QgsProcessingFeedback과
    같은 모양(setProgress/isCanceled)이면 무엇이든 받는다 — 없으면 무시한다.
    extract_attribs는 Pro 빌드에서만 효과가 있다.
    """
    dwg = Path(dwg)
    started = time.monotonic()

    work = Path(tempfile.mkdtemp(prefix="echocad-"))
    try:
        converted = engine.convert(dwg, work / (dwg.stem + ".dxf"), exe=exe)
        if converted.status != "ok":
            return ImportResult(dwg.name, converted.status, converted.note,
                                elapsed_sec=time.monotonic() - started), []

        crs_id = crs or QgsProject.instance().crs().authid()
        # 헤더 선언이 아니라 실제 바이트로 인코딩을 정한다. 한 번만 본다.
        utf8 = dxfenc.content_is_utf8(converted.dxf)

        # 프로파일은 Pro 전용이다. 잠겨 있으면 없는 것으로 본다.
        if profile is not None and not (pro is not None and pro.unlocked()):
            profile = None
        unmatched: list[str] = []

        # OGR는 피처를 지연 읽기하므로 순회가 끝날 때까지 설정을 유지해야 한다.
        with dxf_options(inline_blocks=True, force_utf8=utf8):
            layers = _split_by_cad_layer(converted.dxf, crs_id, feedback, profile, unmatched)

        if layers is None:
            return ImportResult(dwg.name, "error", "DXF를 읽지 못했습니다",
                                elapsed_sec=time.monotonic() - started), []

        dropped = _drop_broken_geometries(layers)
        _apply_cad_colors(layers)
        _enable_text_labels(layers)

        block_layers = []
        if extract_attribs and pro_attribs is not None and pro.unlocked():
            block_layers = pro_attribs.extract_block_layers(
                converted.dxf, crs_id, feedback, force_utf8=utf8
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if feedback is not None and feedback.isCanceled():
        return ImportResult(dwg.name, "error", "사용자가 취소했습니다",
                            elapsed_sec=time.monotonic() - started), []

    if not layers:
        return ImportResult(dwg.name, "empty", "엔티티가 없습니다",
                            elapsed_sec=time.monotonic() - started), []

    produced = list(layers.values()) + block_layers
    if output_gpkg is not None:
        # 저장했으면 저장된 것을 돌려준다. 메모리 레이어를 돌려주면 프로젝트를
        # 저장했다가 다시 열었을 때 레이어가 전부 비어 있다.
        produced = _write_gpkg(produced, Path(output_gpkg))

    results = [
        LayerResult(layer.name(), source, geometry, layer.featureCount())
        for (source, geometry), layer in layers.items()
    ]
    results += [
        LayerResult(layer.name(), layer.name().rsplit("_blocks", 1)[0], "block", layer.featureCount())
        for layer in block_layers
    ]
    return ImportResult(
        file=dwg.name,
        status="ok",
        note=f"좌표가 깨진 피처 {dropped}개를 제외했습니다" if dropped else "",
        layers=results,
        total_features=sum(r.feature_count for r in results),
        dropped_features=dropped,
        unmatched_layers=sorted(unmatched),
        elapsed_sec=time.monotonic() - started,
    ), produced


def _split_by_cad_layer(dxf: Path, crs_id: str, feedback, profile=None, unmatched=None):
    """(출력 레이어명, 지오메트리 종류)마다 메모리 레이어를 하나씩 만든다.

    프로파일이 있으면 여러 CAD 레이어가 한 출력 레이어로 **합쳐진다** — `A-WALL-EXST`와
    `A-WALL-NEW`를 `building_wall` 하나로 모으는 것이 이 기능의 요점이다. 원래 CAD
    레이어명은 피처의 `cad_layer` 속성에 그대로 남으므로 정보는 잃지 않는다.

    QgsVectorLayer가 아니라 osgeo.ogr로 읽는다. CAD 색·글자 크기·각도가 OGR
    **스타일 문자열**로만 오는데 QGIS 레이어로는 그걸 볼 수 없기 때문이다.
    """
    # ponytail: 단일 패스로 메모리에 담는다. 표본 최대가 3만 피처라 여유가 크고,
    # 그보다 커지면 GPKG로 스트리밍 기록하도록 바꾼다.
    source = ogr.Open(str(dxf))
    if source is None:
        return None
    entities = source.GetLayer(0)
    linetype_table = linetypes.read(dxf)
    buckets: dict[tuple[str, str], QgsVectorLayer] = {}
    taken: set[str] = set()

    try:
        _fill_buckets(entities, buckets, taken, linetype_table, crs_id, feedback,
                      profile, unmatched if unmatched is not None else [])
    finally:
        # OGR가 DXF를 붙들고 있으면 Windows에서 임시 파일이 안 지워진다.
        # 취소로 빠져나갈 때도 반드시 놓아야 한다.
        entities = None
        source = None
    return buckets


def _fill_buckets(entities, buckets, taken, linetype_table, crs_id, feedback,
                  profile, unmatched) -> None:
    total = entities.GetFeatureCount() or 1
    seen_unmatched = set()

    for index, feature in enumerate(entities):
        if feedback is not None:
            if feedback.isCanceled():
                buckets.clear()
                return
            feedback.setProgress(int(index / total * 100))

        raw_geometry = feature.GetGeometryRef()
        if raw_geometry is None:
            continue
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(raw_geometry.ExportToIsoWkb()))
        if geometry.isEmpty():
            continue

        style = styles.parse(feature.GetStyleString())
        cad_layer = str(_field(feature, "Layer") or "0")

        rule = None
        if profile is not None:
            rule, matched = profile.rule_for(cad_layer)
            if not matched and cad_layer not in seen_unmatched:
                seen_unmatched.add(cad_layer)
                unmatched.append(cad_layer)
            if rule.geometry == "polygon":
                closed = _as_polygon(geometry)
                if closed is not None:
                    geometry = closed

        shape = _GEOMETRY.get(QgsWkbTypes.geometryType(geometry.wkbType()))
        if shape is None:
            continue
        wkb_name, suffix = shape
        geometry.convertToMultiType()

        output_name = rule.resolve(cad_layer) if rule is not None else cad_layer
        key = (output_name, suffix)
        target = buckets.get(key)
        if target is None:
            target = _new_memory_layer(output_name, suffix, wkb_name, crs_id, taken)
            buckets[key] = target

        text = _text_of(feature)
        if text:
            # 나중에 라벨을 켤 레이어를 여기서 표시해 둔다. 다시 훑지 않으려는 것.
            target.setCustomProperty("echocad/has_text", True)

        copied = QgsFeature(target.fields())
        copied.setGeometry(geometry)
        copied["cad_layer"] = cad_layer
        copied["text"] = text
        copied["color"] = style.color
        copied["fill_color"] = style.fill or ""
        copied["stroke_style"] = linetype_table.style_of(_field(feature, "Linetype"), cad_layer)
        copied["stroke_width"] = style.width
        copied["text_size"] = style.size
        copied["text_angle"] = style.angle
        copied["text_quad"] = style.quadrant
        copied["text_dx"] = style.dx
        copied["text_dy"] = style.dy
        target.dataProvider().addFeature(copied)


def _as_polygon(geometry: QgsGeometry):
    """닫힌 폴리라인을 폴리곤으로. 닫혀 있지 않으면 None을 돌려 원본을 그대로 쓰게 한다."""
    if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.LineGeometry:
        return None

    # 단일파트에 asMultiPolyline을 부르면 예외가 난다. 종류를 먼저 본다.
    parts = geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]
    rings = [part for part in parts if len(part) >= 4 and part[0] == part[-1]]
    if not rings:
        return None
    return QgsGeometry.fromMultiPolygonXY([[ring] for ring in rings])


def _field(feature, name: str):
    index = feature.GetFieldIndex(name)
    return feature.GetField(index) if index >= 0 else None


def _text_of(feature) -> str:
    """진짜 글자 엔티티의 내용만 돌려준다.

    OGR DXF 드라이버는 SOLID 같은 채움 엔티티에도 `Text`를 채운다. 그대로 라벨로
    쓰면 벽마다 "SOLID"가 찍힌다. `SubClasses`로 글자 엔티티인지 확인한다.
    """
    text = _field(feature, "Text")
    if not text or not names.is_text_entity(str(_field(feature, "SubClasses") or "")):
        return ""
    return str(text)


def _apply_cad_colors(buckets) -> None:
    """피처마다 저장해 둔 CAD 색으로 그리게 한다.

    레이어 하나에 여러 색이 섞이므로 심볼 색을 고정하지 않고 필드에 연결한다.
    폴리곤은 원본에 채움이 있을 때만 채운다 — 닫힌 폴리라인까지 칠하면 도면이 뭉갠다.
    """
    stroke = QgsProperty.fromField("color")
    fill = QgsProperty.fromExpression(
        "if(\"fill_color\" is null or \"fill_color\" = '', 'transparent', \"fill_color\")"
    )
    dash = QgsProperty.fromField("stroke_style")
    # CAD 선폭은 도면 단위다. 지정이 없으면 0 — QGIS에서 0은 가장 가는 선이고
    # 이는 CAD의 "기본 선폭" 의미와 맞는다.
    width = QgsProperty.fromExpression(
        "coalesce(\"stroke_width\", 0)"
    )
    # 글자 삽입점에는 마커를 그리지 않는다. CAD는 글자만 보여 준다.
    # 진짜 POINT 엔티티(측점 등)는 그대로 남긴다.
    marker_size = QgsProperty.fromExpression(
        "if(\"text\" is null or \"text\" = '', 1.4, 0)"
    )

    for layer in buckets.values():
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if symbol is None:
            continue
        for position in range(symbol.symbolLayerCount()):
            symbol_layer = symbol.symbolLayer(position)
            symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, stroke)
            if layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, fill)
            else:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, stroke)
            if layer.geometryType() != QgsWkbTypes.PointGeometry:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeWidth, width)
                _use_map_units(symbol_layer)
            if layer.geometryType() == QgsWkbTypes.LineGeometry:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeStyle, dash)
            if layer.geometryType() == QgsWkbTypes.PointGeometry:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertySize, marker_size)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _use_map_units(symbol_layer) -> None:
    """선 폭 단위를 도면 단위로 맞춘다. 클래스마다 설정 함수 이름이 다르다."""
    for setter in ("setWidthUnit", "setStrokeWidthUnit"):
        if hasattr(symbol_layer, setter):
            getattr(symbol_layer, setter)(QgsUnitTypes.RenderMapUnits)
            return


def _enable_text_labels(buckets) -> None:
    """TEXT·MTEXT 내용을 원본 크기·각도·색으로 화면에 띄운다.

    켜 주지 않으면 도면에 글자가 하나도 안 보인다. 속성 테이블에는 들어와 있지만
    CAD 사용자는 화면에서 글자를 기대하므로, 안 보이면 변환이 실패한 것으로 읽힌다.
    """
    for layer in buckets.values():
        if not layer.customProperty("echocad/has_text"):
            continue

        text_format = QgsTextFormat()
        # CAD 글자 높이는 도면 단위다. 화면 배율과 무관하게 원본 크기를 지켜야 한다.
        text_format.setSizeUnit(QgsUnitTypes.RenderMapUnits)
        text_format.setSize(2.0)

        settings = QgsPalLayerSettings()
        settings.fieldName = "text"
        settings.placement = QgsPalLayerSettings.OverPoint
        settings.offsetUnits = QgsUnitTypes.RenderMapUnits
        # CAD는 글자가 겹쳐도 전부 그린다. QGIS 기본값은 겹치면 숨기므로 끈다.
        settings.displayAll = True
        settings.obstacle = False
        settings.setFormat(text_format)

        properties = settings.dataDefinedProperties()
        properties.setProperty(QgsPalLayerSettings.Size, QgsProperty.fromField("text_size"))
        properties.setProperty(QgsPalLayerSettings.LabelRotation, QgsProperty.fromField("text_angle"))
        properties.setProperty(QgsPalLayerSettings.Color, QgsProperty.fromField("color"))
        # 삽입점 기준 글자 방향과 오프셋도 원본을 따른다.
        properties.setProperty(QgsPalLayerSettings.OffsetQuad, QgsProperty.fromField("text_quad"))
        properties.setProperty(
            QgsPalLayerSettings.OffsetXY,
            QgsProperty.fromExpression("concat(\"text_dx\", ',', \"text_dy\")"),
        )
        settings.setDataDefinedProperties(properties)

        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)


def _drop_broken_geometries(buckets) -> int:
    """좌표가 깨진 피처를 버리고 그 수를 돌려준다. 판정 규칙은 outliers.py에 있다."""
    owners = []
    centers = []
    for layer in buckets.values():
        for feature in layer.getFeatures():
            point = feature.geometry().centroid().asPoint()
            owners.append((layer, feature.id()))
            centers.append((point.x(), point.y()))

    doomed: dict = {}
    for index in outliers.outlier_indices(centers):
        layer, fid = owners[index]
        doomed.setdefault(layer, []).append(fid)

    for layer, ids in doomed.items():
        layer.dataProvider().deleteFeatures(ids)

    for layer in buckets.values():
        layer.updateExtents()
    return sum(len(ids) for ids in doomed.values())


def _new_memory_layer(cad_layer: str, suffix: str, wkb_name: str, crs_id: str, taken: set[str]):
    base = f"{names.sanitize(cad_layer)}_{suffix}"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)

    return QgsVectorLayer(f"{wkb_name}?crs={crs_id}{_FIELDS}", name, "memory")


def _write_gpkg(layers: list, gpkg: Path) -> list:
    """레이어들을 GeoPackage에 쓰고, 그 파일을 읽는 레이어들을 돌려준다.

    첫 레이어에서 파일을 새로 만든다. 이어 붙이면 지난 임포트의 레이어가 남아
    이번 결과와 구분되지 않는다.
    """
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    context = QgsProject.instance().transformContext()
    first = True
    saved = []

    for layer in layers:
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer.name()
        options.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteFile if first
            else QgsVectorFileWriter.CreateOrOverwriteLayer
        )
        error, message, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(gpkg), context, options
        )
        if error != QgsVectorFileWriter.NoError:
            raise OSError(f"{layer.name()} 저장 실패: {message}")
        first = False

        stored = QgsVectorLayer(f"{gpkg}|layername={layer.name()}", layer.name(), "ogr")
        if stored.isValid():
            _copy_style(layer, stored)
            saved.append(stored)
        else:
            saved.append(layer)

    return saved


def _copy_style(source: QgsVectorLayer, target: QgsVectorLayer) -> None:
    """색·라벨 설정을 그대로 옮긴다. 저장하면서 스타일을 잃으면 화면이 달라진다."""
    document = QDomDocument()
    source.exportNamedStyle(document)
    target.importNamedStyle(document)
