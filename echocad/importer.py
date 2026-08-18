# DXF를 CAD 레이어별 QGIS 벡터 레이어로 나누는 모듈. 원본 도면과 같은 색·글자로 보이게 스타일까지 옮긴다
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from osgeo import ogr
from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsRendererCategory,
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
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtXml import QDomDocument

from . import dxfenc, engine, hatches, linetypes, names, outliers, styles, xrefs
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
    "&field=fill_color:string&field=hatch:string"
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
    status: str  # ok | unsupported | error | timeout | empty | truncated
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
    project=None,
) -> tuple[ImportResult, list[QgsVectorLayer]]:
    """DWG 하나를 QGIS 레이어들로 바꾼다.

    output_gpkg가 없으면 메모리 레이어를 돌려준다. feedback은 QgsProcessingFeedback과
    같은 모양(setProgress/isCanceled)이면 무엇이든 받는다 — 없으면 무시한다.
    extract_attribs는 Pro 빌드에서만 효과가 있다.

    project는 좌표계와 변환 컨텍스트를 얻을 QgsProject다. Processing은 워커 스레드에서
    도는데 QgsProject.instance()는 메인 스레드 전용이라, 알고리즘이 context.project()를
    넘겨 준다 (QgsProcessingAlgorithm 계약). 안 넘기면 지금 프로젝트를 본다.
    """
    dwg = Path(dwg)
    started = time.monotonic()
    if project is None:
        project = QgsProject.instance()

    work = Path(tempfile.mkdtemp(prefix="echocad-"))
    try:
        converted = engine.convert(dwg, work / (dwg.stem + ".dxf"), exe=exe)
        if converted.status != "ok":
            return ImportResult(dwg.name, converted.status, converted.note,
                                elapsed_sec=time.monotonic() - started), []

        crs_id = crs or project.crs().authid()
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
        absent_refs = xrefs.missing(converted.dxf, dwg)

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
        produced = _write_gpkg(produced, Path(output_gpkg), project)

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
        note=" / ".join(filter(None, [
            f"좌표가 깨진 피처 {dropped}개를 제외했습니다" if dropped else "",
            xrefs.note(absent_refs),
        ])),
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
    hatch_table = hatches.read(dxf)
    buckets: dict[tuple[str, str], QgsVectorLayer] = {}
    taken: set[str] = set()

    try:
        _fill_buckets(entities, buckets, taken, linetype_table, hatch_table, crs_id,
                      feedback, profile, unmatched if unmatched is not None else [])
    finally:
        # OGR가 DXF를 붙들고 있으면 Windows에서 임시 파일이 안 지워진다.
        # 취소로 빠져나갈 때도 반드시 놓아야 한다.
        entities = None
        source = None
    return buckets


def _fill_buckets(entities, buckets, taken, linetype_table, hatch_table, crs_id,
                  feedback, profile, unmatched) -> None:
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
        copied["hatch"] = _hatch_key(hatch_table, feature)
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


def _hatch_key(hatch_table, feature) -> str:
    """이 피처가 패턴 채움 해치면 그 모양을 나타내는 분류값, 아니면 빈 문자열.

    핸들로 찾는다 — OGR이 주는 `EntityHandle`이 DXF의 그룹코드 5와 같은 값이다.
    """
    if not hatch_table:
        return ""
    handle = _field(feature, "EntityHandle")
    if not handle:
        return ""
    hatch = hatch_table.get(str(handle).strip())
    if hatch is None or hatch.solid:
        return ""
    return hatch.key()


def _pattern_fill_symbol(families, stroke) -> "QgsFillSymbol | None":
    """평행선 가족들로 채움 심볼을 만든다. 색은 피처의 CAD 색을 따른다."""
    try:
        from qgis.core import QgsLinePatternFillSymbolLayer, QgsSimpleFillSymbolLayer
    except ImportError:      # 아주 오래된 QGIS. 단색으로 두는 편이 낫다
        return None

    built = []
    for family in families:
        layer = QgsLinePatternFillSymbolLayer()
        layer.setLineAngle(family.angle)
        layer.setDistance(family.spacing)
        layer.setDistanceUnit(QgsUnitTypes.RenderMapUnits)
        # 선 자체는 가늘게. CAD 해치선은 굵기를 따로 갖지 않는다.
        layer.setLineWidth(0.2)
        layer.setLineWidthUnit(QgsUnitTypes.RenderMillimeters)
        # subSymbol()이 준 포인터를 setSubSymbol로 되돌려주면 이중 해제로 죽는다.
        # 제자리에서 고친다.
        sub = layer.subSymbol()
        if sub is not None:
            for position in range(sub.symbolLayerCount()):
                sub.symbolLayer(position).setDataDefinedProperty(
                    QgsSymbolLayer.PropertyStrokeColor, stroke)
            if family.dashes:
                _set_dash_vector(sub, family.dashes)
        built.append(layer)

    if not built:
        return None

    # 경계선은 남긴다. 해치만 그리면 도형의 윤곽이 사라진다.
    outline = QgsSimpleFillSymbolLayer()
    outline.setBrushStyle(Qt.NoBrush)
    outline.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, stroke)
    built.append(outline)

    # QgsFillSymbol()은 레이어가 없는 심볼을 만든다. deleteSymbolLayer(0)을 부르면
    # 범위를 벗어나 프로세스가 죽는다. 목록을 넘겨 한 번에 만든다.
    return QgsFillSymbol(built)


def _set_dash_vector(line_symbol, dashes) -> None:
    """대시 길이를 선 심볼에 넣는다. 실패해도 실선으로 그리면 되므로 조용히 넘어간다."""
    values = [max(v, 0.05) for v in dashes]
    if len(values) % 2:
        values = values + values      # 홀수면 그리기/띄우기 쌍이 안 맞는다
    for position in range(line_symbol.symbolLayerCount()):
        layer = line_symbol.symbolLayer(position)
        if hasattr(layer, "setUseCustomDashPattern"):
            layer.setUseCustomDashPattern(True)
            layer.setCustomDashVector(values)
            if hasattr(layer, "setCustomDashPatternUnit"):
                layer.setCustomDashPatternUnit(QgsUnitTypes.RenderMapUnits)


def _apply_hatch_patterns(layer, base_symbol, stroke) -> bool:
    """이 레이어에 패턴 해치가 있으면 분류 렌더러로 바꾼다.

    채움 패턴은 심볼 레이어의 구조라 표현식으로 못 바꾼다. 그래서 모양마다 분류를
    만든다. 실측상 한 레이어의 서로 다른 조합은 많아야 스물 몇 개다.
    """
    index = layer.fields().indexOf("hatch")
    if index < 0:
        return False
    keys = {value for value in layer.uniqueValues(index) if value}
    if not keys:
        return False

    categories = [QgsRendererCategory("", base_symbol.clone(), "채움 없음")]
    for key in sorted(keys):
        families = _families_from_key(key)
        symbol = _pattern_fill_symbol(families, stroke) if families else None
        categories.append(QgsRendererCategory(
            key, symbol or base_symbol.clone(), key.split("|")[0]))

    layer.setRenderer(QgsCategorizedSymbolRenderer("hatch", categories))
    return True


def _families_from_key(key: str):
    """`_hatch_key`가 만든 문자열을 다시 가족 목록으로. 피처마다 원본을 들고 다니지
    않으려고 분류값 자체에 모양을 담았다."""
    parts = key.split("|")[1:]
    families = []
    for part in parts:
        angle, _, spacing = part.partition("/")
        try:
            families.append(hatches.Family(angle=float(angle), spacing=float(spacing)))
        except ValueError:
            return []
    return families


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
                _use_lineweight_units(symbol_layer)
            if layer.geometryType() == QgsWkbTypes.LineGeometry:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeStyle, dash)
            if layer.geometryType() == QgsWkbTypes.PointGeometry:
                symbol_layer.setDataDefinedProperty(QgsSymbolLayer.PropertySize, marker_size)
        if layer.geometryType() == QgsWkbTypes.PolygonGeometry and                 _apply_hatch_patterns(layer, symbol, stroke):
            continue
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _use_lineweight_units(symbol_layer) -> None:
    """선 폭 단위를 밀리미터로 맞춘다. 클래스마다 설정 함수 이름이 다르다.

    CAD 선폭(DXF 그룹코드 370)은 1/100mm 단위의 **출력 폭**이지 도면 위의 길이가 아니다.
    2026-08-17 실측 — 표본의 370 값이 13·15·35·40·50이고 GDAL은 이를 100으로 나눈
    `w:0.13g`로 내보낸다. 접미가 g(ground)라도 숫자는 밀리미터다.

    도면 단위로 잡으면 미터 기준 좌표계(EPSG:5186 등)에서 0.5가 지상 500mm가 되어
    1:1000 축척에서 도면이 검은 띠로 뭉개진다. 글자 높이(그룹코드 40)는 실제로 도면
    단위라 그쪽은 RenderMapUnits가 맞다 — 성격이 다르므로 같이 묶지 않는다.
    """
    for setter in ("setWidthUnit", "setStrokeWidthUnit"):
        if hasattr(symbol_layer, setter):
            getattr(symbol_layer, setter)(QgsUnitTypes.RenderMillimeters)
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
        # QgsPalLayerSettings.OverPoint를 쓰면 안 된다. 같은 이름이 다른 열거형
        # (LabelPredefinedPointPosition)에도 있어서 import는 되고 대입에서 TypeError가 난다.
        # 2026-08-17 실측 — QGIS 3.44.13과 4.2.1 양쪽에서 재현되고, 아래 형태는 양쪽 다 통과한다.
        settings.placement = Qgis.LabelPlacement.OverPoint
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
            # 빈 지오메트리는 중심점이 나오지 않는다. 그대로 asPoint()를 부르면
            # ValueError가 import_dwg 밖으로 튀어 QGIS 오류 창이 뜬다
            # (2026-08-17 맥 QGIS 4.2.1 실측 — acadsharp-r2000.dwg의 MultiPolygon EMPTY).
            # 좌표 이상치 판정 대상이 아니므로 건너뛴다.
            center = feature.geometry().centroid()
            if center.isNull():
                continue
            point = center.asPoint()
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
    # 이름 짓는 규칙은 names.unique_name 하나만 쓴다. 여기서 루프를 따로 돌리면
    # 대소문자 처리 같은 수정이 한쪽에만 반영된다.
    name = names.unique_name(f"{names.sanitize(cad_layer)}_{suffix}", taken)

    return QgsVectorLayer(f"{wkb_name}?crs={crs_id}{_FIELDS}", name, "memory")


def _write_gpkg(layers: list, gpkg: Path, project=None) -> list:
    """레이어들을 GeoPackage에 쓰고, 그 파일을 읽는 레이어들을 돌려준다.

    첫 레이어에서 파일을 새로 만든다. 이어 붙이면 지난 임포트의 레이어가 남아
    이번 결과와 구분되지 않는다.
    """
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    # 워커 스레드에서 QgsProject.instance()를 만지면 안 된다. import_dwg가 넘겨 준다.
    context = (project or QgsProject.instance()).transformContext()
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
