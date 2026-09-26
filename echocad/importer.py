# DXF를 CAD 레이어별 QGIS 벡터 레이어로 나누는 모듈. 원본 도면과 같은 색·글자로 보이게 스타일까지 옮긴다
from __future__ import annotations

import math
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from osgeo import ogr
from qgis.core import (
    QgsRectangle,
    QgsPointXY,
    QgsMultiPolygon,
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineString,
    QgsPalLayerSettings,
    QgsPoint,
    QgsPolygon,
    QgsProject,
    QgsProperty,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsSymbol,
    QgsSymbolLayer,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtXml import QDomDocument

from . import (dxfenc, hatches, layerstate, linetypes, mtext, names, outliers,
               styles, tableblocks, xrefs)

# 변환 엔진은 무료판 빌드에 들어가지 않는다. 무료판은 DXF 만 읽으므로 변환할 일이
# 없고, 없는 모듈을 최상위에서 임포트하면 플러그인이 통째로 안 뜬다.
try:
    from . import engine
except ImportError:      # 무료판
    engine = None
from .i18n import tr
from .links import PRO_URL as _PRO_URL
from .gdalopts import dxf_options

try:
    from . import pro
    from .pro import attribs as pro_attribs
    from .pro import closerings as pro_closerings
    from .pro import georef as pro_georef
    from .pro import textjoin as pro_textjoin
except ImportError:  # Community 빌드에는 pro/ 폴더가 없다
    pro = None
    pro_attribs = None
    pro_closerings = None
    pro_georef = None
    pro_textjoin = None

# 멀티파트로 통일한다 — DXF는 한 엔티티가 여러 파트인 경우가 흔하고, 단일파트 레이어에
# 넣으면 GeoPackage 규격에 어긋나 드라이버가 경고를 낸다.
# 손상된 엔티티를 건너뛸 때, 같은 자리에서 이만큼 연속 실패하면 스트림이 죽은 것으로 본다.
_MAX_CONSECUTIVE_ERRORS = 20

def _symbol_property(name: str):
    """심볼 레이어 데이터 정의 속성 키. QGIS 버전마다 이름이 다르다.

    QGIS 3.34.9 에는 Property.PropertyStrokeColor 만 있고, 3.44.13 은 Property.StrokeColor 와
    옛 이름을 둘 다 받는다(2026-09-15 실측). QGIS 4 는 새 이름을 쓴다. 옛 이름을 직접 쓰면
    plugins.qgis.org 의 Qt6 검사가 스코프 없는 열거형으로 잡으므로, 있는 쪽을 골라 쓴다.
    """
    prop = QgsSymbolLayer.Property
    member = getattr(prop, name, None)
    return member if member is not None else getattr(prop, "Property" + name)


_STROKE_COLOR = _symbol_property("StrokeColor")
_FILL_COLOR = _symbol_property("FillColor")
_STROKE_WIDTH = _symbol_property("StrokeWidth")
_STROKE_STYLE = _symbol_property("StrokeStyle")
_MARKER_SIZE = _symbol_property("Size")

_GEOMETRY = {
    Qgis.GeometryType.Point: ("MultiPoint", "point"),
    Qgis.GeometryType.Line: ("MultiLineString", "line"),
    Qgis.GeometryType.Polygon: ("MultiPolygon", "polygon"),
}

# cad_handle·cad_type·cad_linetype 는 OGR 이 이미 주는 값이라 읽는 비용이 없다. 담지 않으면
# "이 피처가 원본 도면의 어느 엔티티였나"를 확인하려고 도면을 다시 열어야 한다 —
# 검수·재작성에서 매번 걸리는 지점이다. QGIS 기본 임포터는 이것들을 담는다 (2026-08-23 실측).
#
# 글자 칸은 반드시 `string(0)` 이다. 길이를 안 적으면 255 자가 되고, 그것을 넘는 값이
# 오면 **피처가 통째로 거절된다** - 글자만 잘리는 것이 아니라 도형까지 같이 사라지고
# 오류도 안 뜬다. acadsharp-r2000 의 3,435 자짜리 MTEXT 와 434 자짜리 해치 키가 이렇게
# 없어지고 있었다 (2026-08-26 실측). 도면 주기는 이 길이를 쉽게 넘는다.
_FIELDS = (
    "&field=cad_layer:string(0)"
    "&field=cad_handle:string(0)"
    "&field=cad_type:string(0)"
    "&field=cad_linetype:string(0)"
    "&field=text:string(0)"
    "&field=color:string(0)"
    "&field=fill_color:string(0)&field=hatch:string(0)"
    "&field=stroke_style:string(0)"
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
    status: str  # ok | unsupported | error | timeout | empty | truncated | locked
    note: str = ""
    layers: list[LayerResult] = field(default_factory=list)
    total_features: int = 0
    dropped_features: int = 0  # 좌표가 깨져 제외한 피처 수
    skipped_entities: int = 0  # 읽다가 깨져 건너뛴 엔티티 수
    # 가져온 직후 화면에 잡아 줄 범위 (xmin, ymin, xmax, ymax). 데이터 범위와 다를 수 있다 —
    # 본체에서 멀리 떨어진 정당한 엔티티는 보기에서만 뺀다. outliers.view_bounds 참조.
    view_extent: tuple | None = None
    joined_labels: int = 0     # 도형에 붙인 문자 수 (Pro)
    closed_rings: int = 0      # 열린 선을 닫아 만든 면 수 (Pro)
    unmatched_layers: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    # 가져오기 보고서(importreport). 도면 원문과 가져온 피처를 맞댄 결과.
    report: dict | None = None
    # 빠진 엔티티만 잘라 낸 작은 DXF 글. 사용자가 보내기에 동의할 때만 나간다.
    excerpt: str = ""


def import_dwg(
    dwg: Path,
    output_gpkg: Path | None = None,
    crs: str | None = None,
    exe: Path | None = None,
    feedback=None,
    extract_attribs: bool = True,
    join_text: bool = False,
    close_tolerance: float = 0.0,
    close_tolerance_mm: float = 0.0,
    georef=None,
    keep_z: bool = False,
    skip_hidden: bool = False,
    profile=None,
    project=None,
) -> tuple[ImportResult, list[QgsVectorLayer]]:
    """DWG 하나를 QGIS 레이어들로 바꾼다.

    output_gpkg가 없으면 메모리 레이어를 돌려준다. feedback은 QgsProcessingFeedback과
    같은 모양(setProgress/isCanceled)이면 무엇이든 받는다 — 없으면 무시한다.
    extract_attribs·join_text·close_tolerance·georef·keep_z는 Pro 빌드에서만 효과가 있다.
    skip_hidden은 두 판 모두에서 동작한다 - 가져오기 자체를 다듬는 것이라 유료로 둘 이유가 없다.

    project는 좌표계와 변환 컨텍스트를 얻을 QgsProject다. Processing은 워커 스레드에서
    도는데 QgsProject.instance()는 메인 스레드 전용이라, 알고리즘이 context.project()를
    넘겨 준다 (QgsProcessingAlgorithm 계약). 안 넘기면 지금 프로젝트를 본다.
    """
    dwg = Path(dwg)
    started = time.monotonic()
    if project is None:
        project = QgsProject.instance()

    # 잠긴 상태에서 유료 옵션을 받으면 거절한다. 조용히 버리면 사용자는 성공으로
    # 알고 평면·도면좌표 그대로인 결과를 납품한다. 2026-08-20 에 실제로 재현했다 -
    # 기준점을 채워 넣었는데 status 는 ok 이고 좌표는 그대로였다 (FR-024).
    #
    # 자동으로 켜져 있는 extract_attribs 는 여기서 세우지 않는다. 사용자가 고른 것이
    # 아니라 Pro 의 기본 동작이라, 그것 때문에 평범한 가져오기까지 막으면 안 된다.
    # 대신 건너뛴 사실을 결과에 적는다.
    if pro is not None and not pro.unlocked():
        asked = [name for name, wanted in (
            (tr("mapping profile"), profile is not None),
            (tr("text attaching"), bool(join_text)),
            (tr("polygon closing"), bool(close_tolerance or close_tolerance_mm)),
            (tr("georeferencing"), georef is not None),
            (tr("elevations"), bool(keep_z)),
        ) if wanted]
        if asked:
            return ImportResult(
                dwg.name, "locked",
                tr("These need an active licence and were not applied: {options}").format(
                    options=", ".join(asked)),
                elapsed_sec=time.monotonic() - started), []

    work = Path(tempfile.mkdtemp(prefix="echocad-"))
    try:
        acis_raw: Path | None = None
        acds_raw: Path | None = None
        solids_locked = 0          # Pro 가 아니어서 못 가져온 3차원 솔리드 수
        # DXF 는 그대로 읽는다. 변환기를 태울 이유가 없고, 무료판에는 변환기가 없다.
        if dwg.suffix.lower() == ".dxf":
            source = dwg
        elif engine is None:
            # 무료판이 DWG 를 받은 경우다. 막다른 길로 두지 않는다 - 무료 도구로
            # DXF 로 바꿔 오는 길과 유료판, 둘 다 알려 준다.
            return ImportResult(
                dwg.name, "unsupported",
                # ODA File Converter 를 권하지 않는다 - 공식 FAQ 가 "회원이 아니면
                # 비상업 용도로만" 이라고 못박고 있어, 설계사무소·엔지니어링사인
                # 우리 사용자에게 권하면 약관 위반을 권하는 셈이다 (2026-08-24 확인).
                tr("This edition reads DXF drawings. To open DWG directly, use "
                   "EchoCad Pro, or save the drawing as DXF from the CAD you use.")
                + " " + _dwg_format_note(dwg)
                + " " + tr("Already bought Pro? Get your installer at {url}").format(
                    url=_PRO_URL),
                elapsed_sec=time.monotonic() - started), []
        else:
            # 도면에 든 원본 ACIS 도 함께 받아 둔다. DXF 로 나온 SAT 보다 정확하다
            # (acis 모듈의 SAB 설명 참조).
            acis_raw = work / (dwg.stem + ".acis")
            acds_raw = work / (dwg.stem + ".acds")
            converted = engine.convert(dwg, work / (dwg.stem + ".dxf"), exe=exe,
                                       acis_out=acis_raw, acds_out=acds_raw)
            if converted.status != "ok":
                return ImportResult(dwg.name, converted.status, converted.note,
                                    elapsed_sec=time.monotonic() - started), []
            source = converted.dxf

        # 높이는 Pro 에서만 살린다. 무료판은 지금과 똑같이 평면으로 받는다.
        keep_z = bool(keep_z and pro is not None and pro.unlocked())

        crs_id = crs or project.crs().authid()
        # 헤더 선언이 아니라 실제 바이트로 인코딩을 정한다. 한 번만 본다.
        utf8 = dxfenc.content_is_utf8(source)

        # 프로파일은 Pro 전용이다. 잠겨 있으면 없는 것으로 본다.
        if profile is not None and not (pro is not None and pro.unlocked()):
            profile = None
        unmatched: list[str] = []

        # OGR는 피처를 지연 읽기하므로 순회가 끝날 때까지 설정을 유지해야 한다.
        with dxf_options(inline_blocks=True, force_utf8=utf8):
            # 안 보이게 해 둔 레이어를 거를지. 기본은 다 가져오는 것이다 - 데이터를
            # 조용히 잃는 것보다 목록이 긴 편이 낫다.
            # 인코딩을 OGR 과 맞춰야 한다. 한글 레이어 이름이 깨지면 엔티티의 레이어명과
            # 영영 안 맞아 거르기가 조용히 무효가 된다.
            hidden = layerstate.hidden_layers(source, utf8=utf8) if skip_hidden else set()
            # 규칙 화면에서 체크를 끈 레이어(skip 규칙). 숨긴 레이어와 같은 길로 거르면 선·블록·
            # 솔리드 어디서든 빠진다. 보고서에는 '규칙으로 뺌' 으로 따로 적는다.
            excluded = _excluded_layers(source, utf8, profile)
            hidden = hidden | excluded
            skipped: list[int] = [0]
            layers = _split_by_cad_layer(source, crs_id, feedback, profile, unmatched,
                                         keep_z=keep_z, hidden=hidden, skipped=skipped,
                                         utf8=utf8)

        if layers is None:
            return ImportResult(dwg.name, "error", tr("Could not read the DXF"),
                                elapsed_sec=time.monotonic() - started), []

        dropped, view = _drop_broken_geometries(layers)

        # 거의 닫힌 선을 면으로. 문자 붙이기보다 먼저 해야 새로 생긴 면도 문자를 받는다.
        # 가져오기 창은 mm 로 받는다(close_tolerance_mm) - 도면의 단위로 바꿔 쓴다. 단위가 없는
        # 도면은 mm 로 본다(2026-09-23 지시 "단위는 mm 로 하고 필요하면 100mm 로 하면 되지").
        if close_tolerance_mm and not close_tolerance:
            close_tolerance = close_tolerance_mm / MM_PER_UNIT.get(drawing_units(source) or 4, 1.0)
        closed_rings = 0
        if close_tolerance and pro_closerings is not None and pro.unlocked():
            taken = {layer.name() for layer in layers.values()}
            closed_rings = pro_closerings.apply(
                layers, close_tolerance,
                lambda cad_layer, suffix, wkb: _new_memory_layer(
                    cad_layer, suffix, wkb, crs_id, taken),
                feedback=feedback)

        # 문자를 도형 속성으로. 색을 입히기 전에 해야 라벨 켜기와 순서가 엉키지 않는다.
        joined = 0
        if join_text and pro_textjoin is not None and pro.unlocked():
            joined = pro_textjoin.apply(layers, feedback=feedback)

        # 정합은 마지막이다. 앞의 허용오차들이 도면 단위로 적혀 있으므로, 먼저 옮기면
        # 사용자가 적은 값과 실제로 적용되는 값이 어긋난다.
        transform = None
        if georef is not None and pro_georef is not None and pro.unlocked():
            transform = georef
            pro_georef.apply(layers, transform, feedback=feedback)

        _apply_cad_colors(layers)
        _enable_text_labels(layers)
        absent_refs = xrefs.missing(source, dwg)

        block_layers = []
        # 3차원 솔리드(ACIS). OGR 이 그리지 못하는 것들을 따로 읽는다.
        # **Pro 기능이다.** 무료판에는 몇 개 있는지만 알려 준다 - 조용히 빼면
        # 사용자는 도면이 빈 줄 알고 파일이 깨졌다고 여긴다.
        solid_layer = None
        if pro is not None and pro.unlocked():
            solid_layer = _acis_layer(source, crs_id,
                                      {layer.name() for layer in layers.values()},
                                      acis_raw=acis_raw, acds_raw=acds_raw)
        else:
            solids_locked = acis_entity_count(source)
        if solid_layer is not None and hidden:
            # 솔리드도 숨긴·규칙으로 뺀 레이어면 뺀다. 안 그러면 선은 빠지고 솔리드만 남는다.
            drop = [f.id() for f in solid_layer.getFeatures() if str(f["layer"] or "") in hidden]
            if drop:
                solid_layer.dataProvider().deleteFeatures(drop)
                solid_layer.updateExtents()
            if solid_layer.featureCount() == 0:
                solid_layer = None
        if solid_layer is not None and transform is not None:
            # 솔리드는 위의 정합 뒤에 만들어진다 - 따로 옮긴다. 안 그러면 선만 지도에 가고 솔리드는 남는다.
            pro_georef.apply({"solid": solid_layer}, transform, feedback=feedback)
        if solid_layer is not None:
            layers[(solid_layer.name(), "polygon")] = solid_layer
        # 키가 없거나 만료됐으면 블록 속성을 못 뽑는다. 조용히 넘기지 않고 적어 둔다.
        attribs_skipped = bool(
            extract_attribs and pro_attribs is not None and not pro.unlocked())
        if extract_attribs and pro_attribs is not None and pro.unlocked():
            block_layers = pro_attribs.extract_block_layers(
                source, crs_id, feedback, force_utf8=utf8
            )
            # 블록 속성 레이어도 같이 걸러야 한다. 안 그러면 꺼 둔 레이어 위의 블록만
            # 남아 절반만 걸러진 결과가 된다.
            if hidden:
                block_layers = [layer for layer in block_layers
                                if not _all_from_hidden(layer, hidden)]
            if transform is not None and block_layers:
                pro_georef.apply({i: layer for i, layer in enumerate(block_layers)}, transform,
                                 feedback=feedback)

        # 그릴 것이 없을 때 이유를 말하려면 원본을 아직 볼 수 있을 때 봐야 한다.
        # 아래 finally 가 임시 폴더를 지우므로 여기서 미리 본다 (2026-09-19 - 지운
        # 뒤에 보려다 "No entities" 만 나왔다).
        empty_kinds = _entity_kinds(source) if not layers else []

        # 보고서의 기준(도면 원문)과 빠진 것의 원문도 원본이 있을 때 떠 둔다.
        report_seed = _report_seed(dwg, source, utf8, list(layers.values()) + block_layers,
                                   hidden, pro is not None and pro.unlocked(),
                                   keep_source=solid_layer is not None, excluded=excluded)

    finally:
        shutil.rmtree(work, ignore_errors=True)

    if feedback is not None and feedback.isCanceled():
        return ImportResult(dwg.name, "error", tr("Cancelled"),
                            elapsed_sec=time.monotonic() - started), []

    if not layers:
        # 그릴 것이 없다고 다 같은 말을 하면 안 된다. 도면이 정말 비었을 수도 있고,
        # 우리가 못 그리는 종류만 들었을 수도 있다. 뒤쪽이면 그 종류를 말해 준다.
        note = tr("The drawing holds only {kinds}, which EchoCad does not draw."
                  ).format(kinds=", ".join(empty_kinds)) if empty_kinds else tr("No entities")
        if solids_locked:
            # 위 문장은 "그리지 않는다" 고 하는데 Pro 는 그린다. 솔리드밖에 없으면
            # 아예 갈아 끼운다 - 같은 말을 두 번 하면서 한쪽이 거짓이면 헷갈린다.
            note = (_solids_note(solids_locked)
                    if set(empty_kinds) <= _ACIS_KINDS
                    else " ".join([note, _solids_note(solids_locked)]))
        return ImportResult(dwg.name, "empty", note,
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
    result = ImportResult(
        file=dwg.name,
        status="ok",
        note=" / ".join(filter(None, [
            tr("Dropped {count} features with broken coordinates").format(count=dropped)
        if dropped else "",
            tr("Block attributes were not extracted - the licence is not active")
        if attribs_skipped else "",
            tr("Closed {count} open polylines into polygons").format(count=closed_rings)
        if closed_rings else "",
            tr("Attached {count} texts to shapes").format(count=joined) if joined else "",
            _solids_note(solids_locked),
            xrefs.note(absent_refs),
        ])),
        layers=results,
        total_features=sum(r.feature_count for r in results),
        dropped_features=dropped,
        skipped_entities=skipped[0],
        view_extent=view,
        joined_labels=joined,
        closed_rings=closed_rings,
        unmatched_layers=sorted(unmatched),
        elapsed_sec=time.monotonic() - started,
    )
    _finish_report(result, report_seed)
    return result, produced


def _excluded_layers(source: Path, utf8: bool, profile) -> set[str]:
    """프로파일에서 skip 규칙에 걸리는 도면 레이어 이름들."""
    if profile is None or not any(rule.skip for rule in profile.rules):
        return set()
    return {name for name in layerstate.layer_colors(source, utf8=utf8)
            if profile.rule_for(name)[0].skip}


def _report_seed(dwg: Path, source: Path, utf8: bool, layers: list, hidden, pro_unlocked: bool,
                 keep_source: bool, excluded=frozenset()) -> dict | None:
    """보고서 재료를 원본이 지워지기 전에 모은다. 실패해도 가져오기는 계속된다."""
    try:
        from . import census, importreport

        drawing = census.read(source, utf8=utf8)
        handles = set()
        for layer in layers:
            if layer.fields().indexOf("cad_handle") < 0:
                continue
            for feature in layer.getFeatures():
                handles.add(str(feature["cad_handle"] or ""))
        seed = {"census": drawing, "handles": handles, "hidden": set(hidden or ()) - set(excluded),
                "excluded": set(excluded),
                "pro": pro_unlocked, "dxf_version": drawing.header_version}
        if keep_source:
            # 3D 각도 가져오기 보고서가 빠진 솔리드의 원문을 잘라 낼 수 있게 이 세션 동안만 둔다
            remember_source(dwg.name, source)
        return seed
    except Exception:          # 보고서 때문에 가져오기를 망치지 않는다
        return None


def _finish_report(result: ImportResult, seed: dict | None) -> None:
    if seed is None:
        return
    try:
        from . import census, importreport

        result.report = importreport.build_import_report(
            result.file, seed["census"], seed["handles"], hidden_layers=seed["hidden"],
            excluded_layers=seed.get("excluded", ()),
            pro=seed["pro"], elapsed=result.elapsed_sec, layers=len(result.layers),
            features=result.total_features, dropped=result.dropped_features,
            notes=[result.note], dxf_version=seed["dxf_version"])
        wanted = importreport.missing_handles(result.report)
        if wanted:
            result.excerpt, taken = census.excerpt(seed["census"], wanted)
            result.report["attachable"] = len(taken)
    except Exception:
        result.report = None


# 세션 동안 붙들어 둔 원본 DXF(솔리드가 든 도면만). 3D 각도 보고서의 첨부용.
_SOURCES: dict[str, Path] = {}


def remember_source(name: str, source: Path) -> None:
    import atexit

    keep = Path(tempfile.mkdtemp(prefix="echocad-src-"))
    target = keep / Path(source).name
    shutil.copyfile(source, target)
    old = _SOURCES.get(name)
    _SOURCES[name] = target
    if old is not None:
        shutil.rmtree(old.parent, ignore_errors=True)
    atexit.register(shutil.rmtree, keep, True)


# 규칙 화면이 보여 줄 도면 레이어 한 줄. kind 는 그 레이어에 가장 많은 것(line/area/text/block/dim/point).
_KIND_GROUP = {
    "LINE": "line", "LWPOLYLINE": "line", "POLYLINE": "line", "ARC": "line", "CIRCLE": "line",
    "ELLIPSE": "line", "SPLINE": "line", "MLINE": "line", "XLINE": "line", "RAY": "line", "LEADER": "dim",
    "HATCH": "area", "SOLID": "area", "3DFACE": "area", "REGION": "area", "3DSOLID": "area", "MESH": "area",
    "TEXT": "text", "MTEXT": "text", "ATTDEF": "text", "INSERT": "block",
    "DIMENSION": "dim", "MLEADER": "dim", "MULTILEADER": "dim", "TOLERANCE": "dim", "POINT": "point",
}


# 한 번 가져온 레이어들의 표시(레이어 사용자 속성). '지도에 맞추기' 가 같은 import_id 를 함께 옮긴다.
DRAWING_KEY = "echocad/drawing"
IMPORT_KEY = "echocad/import_id"


# 도면 헤더 $INSUNITS - 도면의 숫자 1 이 무엇인가. 0 이면 설계자가 정하지 않은 것(단위 없음).
UNIT_NAMES = {1: "in", 2: "ft", 3: "mi", 4: "mm", 5: "cm", 6: "m", 7: "km", 8: "µin", 9: "mil",
              10: "yd", 11: "Å", 12: "nm", 13: "µm", 14: "dm", 15: "dam", 16: "hm", 17: "Gm",
              18: "AU", 19: "ly", 20: "pc"}
# 단위 하나가 몇 mm 인가 - 가져오기 창의 mm 값을 도면 단위로 바꿀 때 쓴다
MM_PER_UNIT = {1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0, 7: 1e6, 8: 2.54e-5, 9: 0.0254,
               10: 914.4, 11: 1e-7, 12: 1e-6, 13: 1e-3, 14: 100.0, 15: 1e4, 16: 1e5, 17: 1e12,
               18: 1.495978707e14, 19: 9.4607304725808e18, 20: 3.0856775814914e19}


def drawing_units(dxf: Path) -> int | None:
    """DXF 헤더의 $INSUNITS 값. 없거나 읽지 못하면 None. 헤더만 본다(앞부분에서 끊는다)."""
    try:
        with Path(dxf).open("r", encoding="utf-8", errors="replace") as handle:
            lines = []
            for raw in handle:
                value = raw.strip()
                lines.append(value)
                if len(lines) >= 3 and lines[-3] == "$INSUNITS":
                    return int(value)
                if value == "ENDSEC" and len(lines) > 4:
                    return None
                del lines[:-3]
    except (OSError, ValueError):
        return None
    return None


def drawing_info(path: Path, exe: Path | None = None) -> dict:
    """도면의 {layers: [{name, count, kind, color}], units: $INSUNITS 또는 None}.

    DWG 는 변환기로 잠깐 DXF 를 만들어 한 번에 읽는다(Pro). 읽지 못하면 빈 목록과 None.
    """
    from collections import Counter

    from . import census

    path = Path(path)
    work = None
    try:
        if path.suffix.lower() == ".dxf":
            source = path
        elif engine is None:
            return {"layers": [], "units": None}
        else:
            work = Path(tempfile.mkdtemp(prefix="echocad-layers-"))
            converted = engine.convert(path, work / (path.stem + ".dxf"), exe=exe)
            if converted.status != "ok":
                return {"layers": [], "units": None}
            source = converted.dxf
        utf8 = dxfenc.content_is_utf8(source)
        colors = layerstate.layer_colors(source, utf8=utf8)
        counts: dict[str, Counter] = {}
        for e in census.read(source, utf8=utf8).entities:
            counts.setdefault(e.layer, Counter())[_KIND_GROUP.get(e.kind, "other")] += 1
        layers = [{"name": name, "count": sum(c.values()), "kind": c.most_common(1)[0][0],
                   "color": colors.get(name, "#9a9ea8")} for name, c in counts.items()]
        return {"layers": layers, "units": drawing_units(source)}
    except Exception:
        return {"layers": [], "units": None}
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def drawing_layers(path: Path, exe: Path | None = None) -> list[dict]:
    """도면의 레이어마다 {name, count, kind, color}. 개체가 없는 레이어는 뺀다."""
    return drawing_info(path, exe)["layers"]


def remembered_source(name: str) -> Path | None:
    path = _SOURCES.get(name)
    return path if path is not None and path.exists() else None


def _split_by_cad_layer(dxf: Path, crs_id: str, feedback, profile=None, unmatched=None,
                        keep_z: bool = False, hidden=None, skipped=None, utf8=False):
    """(출력 레이어명, 지오메트리 종류)마다 메모리 레이어를 하나씩 만든다.

    프로파일이 있으면 여러 CAD 레이어가 한 출력 레이어로 **합쳐진다** — `A-WALL-EXST`와
    `A-WALL-NEW`를 `building_wall` 하나로 모으는 것이 이 기능의 요점이다. 원래 CAD
    레이어명은 피처의 `cad_layer` 속성에 그대로 남으므로 정보는 잃지 않는다.

    QgsVectorLayer가 아니라 osgeo.ogr로 읽는다. CAD 색·글자 크기·각도가 OGR
    **스타일 문자열**로만 오는데 QGIS 레이어로는 그걸 볼 수 없기 때문이다.
    """
    # ponytail: 단일 패스로 메모리에 담는다. 표본 최대가 3만 피처라 여유가 크고,
    # 그보다 커지면 GPKG로 스트리밍 기록하도록 바꾼다.
    # GDAL 3.12(QGIS 4)부터 UseExceptions 가 기본이라 읽기 오류가 RuntimeError 로
    # 튄다. 그대로 두면 QGIS 빌드머신 내부 경로가 박힌 원문이 사용자 오류창에
    # 그대로 뜬다 (2026-08-22 실측 — 잘린 DXF 에서 재현). None 을 돌려주면
    # 호출 쪽이 "DXF 를 읽지 못했습니다" 로 안내한다.
    try:
        source = ogr.Open(str(dxf))
    except RuntimeError:
        return None
    if source is None:
        return None
    entities = source.GetLayer(0)
    linetype_table = linetypes.read(dxf)
    hatch_table = hatches.read(dxf)
    # OGR 이 쌓은 분수를 뭉갠다. 서식 코드가 든 MTEXT 만 우리가 다시 푼다.
    mtext_table = mtext.read(dxf)
    buckets: dict[tuple[str, str], QgsVectorLayer] = {}
    taken: set[str] = set()

    # 두 번 훑는 동안 같은 글자를 두 번 담지 않으려고 함께 쓴다.
    placed_text: set = set()
    try:
        _fill_buckets(entities, buckets, taken, linetype_table, hatch_table,
                      mtext_table, crs_id,
                      feedback, profile, unmatched if unmatched is not None else [],
                      keep_z=keep_z, hidden=hidden or set(),
                      skipped=skipped if skipped is not None else [0],
                      placed_text=placed_text)
        _add_table_blocks(dxf, buckets, taken, linetype_table, hatch_table, mtext_table,
                          crs_id, profile, unmatched if unmatched is not None else [],
                          keep_z=keep_z, hidden=hidden or set(),
                          skipped=skipped if skipped is not None else [0],
                          placed_text=placed_text, utf8=utf8)
    except RuntimeError:
        # 여기까지 읽은 것은 살린다. 하나도 못 읽었을 때만 실패로 본다.
        if not buckets:
            return None
    finally:
        # OGR가 DXF를 붙들고 있으면 Windows에서 임시 파일이 안 지워진다.
        # 취소로 빠져나갈 때도 반드시 놓아야 한다.
        entities = None
        source = None
    return buckets


def _add_table_blocks(dxf: Path, buckets, taken, linetype_table, hatch_table,
                      mtext_table, crs_id, profile, unmatched, keep_z, hidden,
                      skipped, placed_text=None, utf8=False) -> None:
    """도면 안 표(TABLE)의 내용을 마저 담는다.

    왜 따로 읽나 — 변환기가 표를 놓는 지시를 못 써서, 표 내용이 든 `*T` 블록이
    아무 데도 놓이지 않은 채 남는다. 그대로 두면 사용자에게는 표가 통째로 사라진다.
    그 블록 안 엔티티는 이미 도면 좌표를 갖고 있으므로 그대로 담으면 제자리에 앉는다.
    자세한 근거는 tableblocks 머리 주석에 있다 (2026-08-26 실측).

    블록을 안 펼친 상태로 한 번 더 열어야 한다 — 펼친 목록에는 놓이지 않은 블록의
    내용이 아예 안 나온다. 표가 없는 도면에서는 파일을 다시 열지 않는다.
    """
    wanted = tableblocks.read(dxf)
    if not wanted:
        return

    # 인코딩을 첫 훑기와 똑같이 맞춰야 한다. 안 맞추면 GDAL 이 헤더의 옛 코드페이지로
    # 읽어 유니코드 글자가 깨진다 - `∅45,6` 이 `â45,6` 이 됐다 (2026-08-27 실측).
    with dxf_options(inline_blocks=False, force_utf8=utf8):
        try:
            source = ogr.Open(str(dxf))
        except RuntimeError:
            return
        if source is None:
            return
        try:
            blocks = source.GetLayerByName("blocks")
            if blocks is None:
                return
            _fill_buckets(blocks, buckets, taken, linetype_table, hatch_table,
                          mtext_table, crs_id, None, profile, unmatched,
                          keep_z=keep_z, hidden=hidden, skipped=skipped,
                          only_handles=wanted, placed_text=placed_text)
        except RuntimeError:
            # 표를 못 담아도 도면 나머지는 이미 들어와 있다. 여기서 실패로 만들지 않는다.
            pass
        finally:
            blocks = None
            source = None


def _iter_entities(entities, skipped):
    """엔티티 하나가 깨져도 도면 전체를 잃지 않게 OGR 순회를 감싼다.

    GDAL 3.12 부터 UseExceptions 가 기본이라 손상된 엔티티 하나가 RuntimeError 로
    튄다. 파이썬 for 문으로 돌리면 그 앞까지 읽은 것까지 통째로 버려진다.
    2026-08-23 실측 — 표본 `acadsharp-r14` 에서 정상 피처 138 개가 18158 번째 줄의
    엔티티 하나 때문에 전부 사라졌다. 같은 파일을 QGIS 기본 임포터는 14 개 레이어로
    읽어 낸다.
    """
    consecutive = 0
    while True:
        try:
            feature = entities.GetNextFeature()
        except RuntimeError:
            skipped[0] += 1
            consecutive += 1
            # 같은 자리에서 계속 실패하면 스트림이 죽은 것이다. 무한 루프를 막는다.
            if consecutive >= _MAX_CONSECUTIVE_ERRORS:
                return
            continue
        consecutive = 0
        if feature is None:
            return
        yield feature


def _fill_buckets(entities, buckets, taken, linetype_table, hatch_table,
                  mtext_table, crs_id,
                  feedback, profile, unmatched, keep_z: bool = False, hidden=frozenset(),
                  skipped=None, only_handles=None, placed_text=None) -> None:
    """OGR 레이어 하나를 훑어 버킷에 담는다.

    only_handles 를 주면 그 핸들만 담는다. 표 블록을 마저 담을 때 쓴다 - 블록 목록에는
    도면에 안 놓인 블록의 내용도 다 들어 있어서, 그대로 담으면 안 보여야 할 것이 나온다.
    """
    # DXF 는 개수를 알려면 파일 전체를 훑어야 한다. 손상된 엔티티가 하나라도 있으면
    # 루프에 들어가기도 전에 여기서 RuntimeError 가 난다 (2026-08-23 실측 —
    # `acadsharp-r14` 가 0.1 초 만에 실패한 자리가 여기였다). 개수는 진행률 표시에만
    # 쓰이므로, 못 세면 진행률을 포기하고 읽기는 계속한다.
    try:
        total = entities.GetFeatureCount() or 1
    except RuntimeError:
        total = 0
    seen_unmatched = set()
    if placed_text is None:
        placed_text = set()
    if skipped is None:
        skipped = [0]

    for index, feature in enumerate(_iter_entities(entities, skipped)):
        if feedback is not None:
            if feedback.isCanceled():
                buckets.clear()
                return
            if total:
                feedback.setProgress(int(index / total * 100))

        if only_handles is not None:
            if str(_field(feature, "EntityHandle") or "") not in only_handles:
                continue

        raw_geometry = feature.GetGeometryRef()
        if raw_geometry is None:
            continue
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(raw_geometry.ExportToIsoWkb()))
        if geometry.isEmpty():
            continue

        style = styles.parse(feature.GetStyleString())
        cad_layer = str(_field(feature, "Layer") or "0")
        if cad_layer in hidden:
            continue

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
        if keep_z:
            # 레이어가 3차원이면 2차원 피처도 Z 를 갖고 있어야 들어간다. 없으면 0 으로 둔다.
            wkb_name += "Z"
            if not geometry.constGet().is3D():
                geometry.get().addZValue(0.0)
        elif geometry.constGet().is3D():
            # 반대쪽도 막아야 한다. 높이를 안 살리는데 Z 가 붙은 피처가 오면 2차원
            # 레이어가 그것을 조용히 거절한다 - 오류도 없이 그 도형만 사라진다.
            # acadsharp-r2000 의 긴 MTEXT 하나가 이렇게 없어지고 있었다 (2026-08-26 실측).
            geometry.get().dropZValue()

        output_name = rule.resolve(cad_layer) if rule is not None else cad_layer
        key = (output_name, suffix)
        target = buckets.get(key)
        if target is None:
            target = _new_memory_layer(output_name, suffix, wkb_name, crs_id, taken)
            buckets[key] = target

        text = _text_of(feature, is_point=(suffix == "point"),
                        mtext_table=mtext_table)
        if text:
            # 같은 글자가 같은 자리에 두 번 들어오는 것을 막는다. 치수 글자를 블록에서
            # 마저 담을 때, GDAL 이 이미 그려 준 것과 겹칠 수 있다 (2026-08-27 실측).
            spot = (text, round(geometry.boundingBox().center().x(), 3),
                    round(geometry.boundingBox().center().y(), 3))
            if spot in placed_text:
                continue
            placed_text.add(spot)
        if text:
            # 나중에 라벨을 켤 레이어를 여기서 표시해 둔다. 다시 훑지 않으려는 것.
            target.setCustomProperty("echocad/has_text", True)

        copied = QgsFeature(target.fields())
        copied.setGeometry(geometry)
        raw_linetype = _field(feature, "Linetype")
        copied["cad_layer"] = cad_layer
        copied["cad_handle"] = str(_field(feature, "EntityHandle") or "")
        copied["cad_type"] = names.entity_type(_field(feature, "SubClasses"))
        copied["cad_linetype"] = str(raw_linetype or "")
        copied["text"] = text
        copied["color"] = style.color
        copied["fill_color"] = style.fill or ""
        copied["hatch"] = _hatch_key(hatch_table, feature)
        copied["stroke_style"] = linetype_table.style_of(raw_linetype, cad_layer)
        copied["stroke_width"] = style.width
        copied["text_size"] = style.size
        copied["text_angle"] = style.angle
        copied["text_quad"] = style.quadrant
        dx, dy = style.dx, style.dy
        if (dx or dy) and _offset_to_origin(geometry, dx, dy):
            # 기본 맞춤(왼쪽·기준선) 글자는 정렬점(그룹 11)이 (0,0) 으로 비어 있다. GDAL 은 그 빈 점까지를
            # 어긋남으로 적어, 글자가 도면 원점 쪽으로 몰렸다(2026-09-23 building-a-floor0 의 ATTRIB).
            # 어긋남이 정확히 '삽입점 → 원점' 이면 진짜 어긋남이 아니다 - 버린다.
            dx, dy = 0.0, 0.0
        copied["text_dx"] = dx
        copied["text_dy"] = dy
        if not target.dataProvider().addFeature(copied):
            # 넣기가 실패하면 그 도형은 사라진다. 세지 않으면 아무도 모른다 -
            # 위의 Z 문제를 이 숫자가 없어서 오래 못 봤다.
            if skipped is not None:
                skipped[0] += 1


# OGR 이 그리지 못해 우리가 따로 읽는 종류. Pro 기능이다.
# OGR 이 그리지 못하는 것들. 3DFACE 는 여기 없다 - 그건 ACIS 가 아니라 그냥
# 메시 면이고 OGR 이 그린다. 넣으면 Pro 가 가져올 것도 아닌 것을 센다.
_ACIS_KINDS = frozenset({
    "3DSOLID", "REGION", "BODY", "SURFACE",
    "EXTRUDEDSURFACE", "LOFTEDSURFACE", "NURBSURFACE", "PLANESURFACE",
    "REVOLVEDSURFACE", "SWEPTSURFACE",
})

_DWG_FORMATS = {
    "AC1009": "R11/R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "R2000",
    "AC1018": "R2004", "AC1021": "R2007", "AC1024": "R2010", "AC1027": "R2013",
    "AC1032": "R2018",
}


def _dwg_format_note(dwg: Path) -> str:
    """DWG 머리말 6바이트만 보고 형식 이름을 알려준다.

    무료판이 DWG 를 받았을 때 "무엇을 받았는지" 조차 말해 주지 않으면, 사용자는
    자기 파일이 잘못된 줄 안다. 앞 6바이트는 암호화되지 않아 그냥 읽힌다.
    """
    try:
        with open(dwg, "rb") as fh:
            sig = fh.read(6).decode("ascii", "replace")
    except OSError:
        return ""
    name = _DWG_FORMATS.get(sig)
    return tr("This is a DWG {format} file.").format(format=name) if name else ""


def _solids_note(count: int) -> str:
    """Pro 가 아니어서 못 가져온 3차원 솔리드를 알린다.

    무료판이 "무엇을 더 가져올 수 있는지"를 숫자로 보여 주는 자리다. 막연한
    "업그레이드하세요" 는 아무도 안 누른다. 몇 개인지 알아야 값이 보인다.
    """
    if not count:
        return ""
    return (tr("3D solids in this drawing: {count}. EchoCad Pro brings them in "
               "with their curved surfaces.").format(count=count)
            + " " + _PRO_URL)


def _entity_counts(path: Path) -> dict[str, int]:
    """ENTITIES 섹션에 어떤 종류의 엔티티가 몇 개 있는지.

    DXF 는 (코드, 값) 두 줄이 한 쌍이다. `0/SECTION` 다음의 `2/<이름>` 이 섹션
    이름이고, 섹션 안에서 `0/<이름>` 이 엔티티 종류다.

    **글자만 센다.** ACIS 를 어떻게 푸는지는 여기 없다 - 무료판에 들어가는 코드라
    읽는 방법을 알려 주면 안 된다.
    """
    counts: dict[str, int] = {}
    section = None
    pending = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                value = raw.strip()
                if pending is None:
                    pending = value
                    continue
                code, pending = pending, None
                if code == "0":
                    if value == "SECTION":
                        section = "?"
                    elif value == "ENDSEC":
                        section = None
                    elif section == "ENTITIES":
                        counts[value] = counts.get(value, 0) + 1
                elif code == "2" and section == "?":
                    section = value
    except OSError:
        return {}
    return counts


def acis_entity_count(path: Path) -> int:
    """도면에 든 3차원 솔리드 엔티티 수. 무료판이 "몇 개 있는지" 를 말하려고 센다.

    엔티티 **이름만** 센다. 안에 든 ACIS 는 건드리지 않는다 - 그건 Pro 기능이고,
    푸는 코드(acis.py)는 무료판에 들어가지 않는다.
    """
    counts = _entity_counts(path)
    return sum(counts.get(kind, 0) for kind in _ACIS_KINDS)


def _entity_kinds(path: Path, limit: int = 6) -> list[str]:
    """ENTITIES 섹션에 어떤 종류의 엔티티가 있는지. 그릴 것이 없을 때 이유를 말하려고 센다.

    왜 필요한가 - 3DSOLID 하나만 든 도면을 "No entities" 라고 알리면 사용자는 파일이
    깨진 줄 안다. 실제로는 엔티티가 있고 우리가 못 그리는 것이다 (2026-09-19 실측 -
    autodesk_visualization_aerial 이 그렇다). OGR 이 피처를 못 만들어도 원본 DXF 에는
    남아 있으므로 파일을 훑어 종류만 센다.

    DXF 는 (코드, 값) 두 줄이 한 쌍이다. `0/SECTION` 다음의 `2/<이름>` 이 섹션 이름이고,
    섹션 안에서 `0/<이름>` 이 엔티티 종류다.
    """
    counts = _entity_counts(path)
    # 종류만 말한다. 개수까지 넣으면 번역문에서 문장이 어색해진다.
    return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _all_from_hidden(layer, hidden) -> bool:
    """이 블록 레이어가 통째로 숨김 CAD 레이어에서 온 것인지.

    블록 참조 레이어는 CAD 레이어 하나에서 만들어지므로 첫 피처만 보면 된다.
    비어 있으면 지울 이유가 없다.
    """
    for feature in layer.getFeatures():
        try:
            return str(feature["cad_layer"]) in hidden
        except KeyError:
            return False
    return False


def _as_polygon(geometry: QgsGeometry):
    """닫힌 폴리라인을 폴리곤으로. 닫혀 있지 않으면 None을 돌려 원본을 그대로 쓰게 한다."""
    if QgsWkbTypes.geometryType(geometry.wkbType()) != Qgis.GeometryType.Line:
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


def _text_of(feature, is_point: bool = True, mtext_table=None) -> str:
    """진짜 글자 엔티티의 내용만 돌려준다.

    OGR DXF 드라이버는 HATCH 에도 `Text`를 채운다. 그대로 라벨로 쓰면 벽마다
    해치 이름이 찍힌다. 그래서 걸러야 한다.

    거르는 방법이 두 가지인 이유 —
    R12(AC1009) DXF 에는 `AcDbEntity:AcDbText` 같은 **서브클래스 표시가 아예 없다.**
    그 형식이 서브클래스보다 먼저 나왔기 때문이다. SubClasses 만 보면 R12 도면의
    글자가 통째로 사라진다 (2026-08-24 실측 — `fortcollins-lincoln-section` 의 393 개).
    그래서 표시가 없으면 지오메트리로 가린다. 글자는 점으로 오고 해치는 면으로 온다 —
    표본 전체에서 예외가 없었다.
    """
    text = _field(feature, "Text")
    if not text:
        return ""
    subclasses = str(_field(feature, "SubClasses") or "")
    if subclasses and not names.is_text_entity(subclasses):
        return ""
    if not subclasses and not is_point:
        return ""

    # 서식 코드가 든 MTEXT 만 우리가 푼 값으로 바꾼다. OGR 은 쌓은 분수를 뭉개
    # `4'-0 1/4"` 를 `4'-014` 로 준다 (2026-08-26 실측). 코드가 없는 글자는 OGR
    # 값이 이미 맞으므로 건드리지 않는다.
    if mtext_table:
        ours = mtext_table.resolve(_field(feature, "EntityHandle"), text)
        if ours:
            return ours
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
        layer.setDistanceUnit(Qgis.RenderUnit.MapUnits)
        # 선 자체는 가늘게. CAD 해치선은 굵기를 따로 갖지 않는다.
        layer.setLineWidth(0.2)
        layer.setLineWidthUnit(Qgis.RenderUnit.Millimeters)
        # subSymbol()이 준 포인터를 setSubSymbol로 되돌려주면 이중 해제로 죽는다.
        # 제자리에서 고친다.
        sub = layer.subSymbol()
        if sub is not None:
            for position in range(sub.symbolLayerCount()):
                sub.symbolLayer(position).setDataDefinedProperty(
                    _STROKE_COLOR, stroke)
            if family.dashes:
                _set_dash_vector(sub, family.dashes)
        built.append(layer)

    if not built:
        return None

    # 경계선은 남긴다. 해치만 그리면 도형의 윤곽이 사라진다.
    outline = QgsSimpleFillSymbolLayer()
    outline.setBrushStyle(Qt.BrushStyle.NoBrush)
    outline.setDataDefinedProperty(_STROKE_COLOR, stroke)
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
                layer.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)


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

    categories = [QgsRendererCategory("", base_symbol.clone(), tr("No pattern"))]
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
            symbol_layer.setDataDefinedProperty(_STROKE_COLOR, stroke)
            if layer.geometryType() == Qgis.GeometryType.Polygon:
                symbol_layer.setDataDefinedProperty(_FILL_COLOR, fill)
            else:
                symbol_layer.setDataDefinedProperty(_FILL_COLOR, stroke)
            if layer.geometryType() != Qgis.GeometryType.Point:
                symbol_layer.setDataDefinedProperty(_STROKE_WIDTH, width)
                _use_lineweight_units(symbol_layer)
            if layer.geometryType() == Qgis.GeometryType.Line:
                symbol_layer.setDataDefinedProperty(_STROKE_STYLE, dash)
            if layer.geometryType() == Qgis.GeometryType.Point:
                symbol_layer.setDataDefinedProperty(_MARKER_SIZE, marker_size)
        if layer.geometryType() == Qgis.GeometryType.Polygon and                 _apply_hatch_patterns(layer, symbol, stroke):
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
            getattr(symbol_layer, setter)(Qgis.RenderUnit.Millimeters)
            return


def _offset_to_origin(geometry, dx: float, dy: float) -> bool:
    """글자 어긋남이 삽입점에서 도면 원점(0,0)까지와 같은가 - 비어 있는 정렬점을 GDAL 이 옮겨 적은 것."""
    if geometry is None or geometry.isEmpty():
        return False
    point = geometry.vertexAt(0)
    # OGR 스타일 문자열의 숫자는 몇 자리에서 잘려 온다(-20.6334) - 그만큼은 봐준다
    tolerance = max(1e-3, 1e-5 * max(abs(point.x()), abs(point.y())))
    return abs(point.x() + dx) <= tolerance and abs(point.y() + dy) <= tolerance


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
        text_format.setSizeUnit(Qgis.RenderUnit.MapUnits)
        text_format.setSize(2.0)

        settings = QgsPalLayerSettings()
        settings.fieldName = "text"
        # QgsPalLayerSettings.OverPoint를 쓰면 안 된다. 같은 이름이 다른 열거형
        # (LabelPredefinedPointPosition)에도 있어서 import는 되고 대입에서 TypeError가 난다.
        # 2026-08-17 실측 — QGIS 3.44.13과 4.2.1 양쪽에서 재현되고, 아래 형태는 양쪽 다 통과한다.
        settings.placement = Qgis.LabelPlacement.OverPoint
        settings.offsetUnits = Qgis.RenderUnit.MapUnits
        # CAD는 글자가 겹쳐도 전부 그린다. QGIS 기본값은 겹치면 숨기므로 끈다.
        settings.displayAll = True
        settings.obstacle = False
        settings.setFormat(text_format)

        properties = settings.dataDefinedProperties()
        properties.setProperty(QgsPalLayerSettings.Property.Size, QgsProperty.fromField("text_size"))
        # CAD 의 글자 각도는 반시계, QGIS 라벨 회전은 시계 방향이다 - 부호를 뒤집어야 도면과 같게 선다
        # (2026-09-23 렌더 시험으로 확인. 전에는 기울어진 글자가 반대로 기울었다).
        properties.setProperty(QgsPalLayerSettings.Property.LabelRotation, QgsProperty.fromExpression('-"text_angle"'))
        properties.setProperty(QgsPalLayerSettings.Property.Color, QgsProperty.fromField("color"))
        # 삽입점 기준 글자 방향과 오프셋도 원본을 따른다.
        properties.setProperty(QgsPalLayerSettings.Property.OffsetQuad, QgsProperty.fromField("text_quad"))
        properties.setProperty(
            QgsPalLayerSettings.Property.OffsetXY,
            QgsProperty.fromExpression("concat(\"text_dx\", ',', \"text_dy\")"),
        )
        settings.setDataDefinedProperties(properties)

        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)


def _drop_broken_geometries(buckets):
    """좌표가 깨진 피처를 버린다. (버린 수, 첫 화면 범위)를 돌려준다.

    화면 범위를 여기서 같이 내는 이유 — 모든 피처의 중심점을 이미 모으고 있어서
    한 번 더 훑을 필요가 없다. 판정 규칙은 둘 다 outliers.py에 있다.
    """
    owners = []
    centers = []
    for layer in buckets.values():
        for feature in layer.getFeatures():
            # 빈 지오메트리는 중심점이 나오지 않는다. 그대로 asPoint()를 부르면
            # ValueError가 import_dwg 밖으로 튀어 QGIS 오류 창이 뜬다
            # (2026-08-17 맥 QGIS 4.2.1 실측 — acadsharp-r2000.dwg의 MultiPolygon EMPTY).
            # 좌표 이상치 판정 대상이 아니므로 건너뛴다.
            #
            # 반드시 **원본** 지오메트리를 본다. 빈 것의 centroid 는 isNull 도 isEmpty 도
            # False 를 돌려주면서 초기화되지 않은 좌표를 들고 있다 (3.34.9 실측). 그것을
            # 담으면 1.3e-311 같은 값이 첫 화면 범위가 되어 도면이 엉뚱한 곳에 잡힌다.
            shape = feature.geometry()
            if shape.isEmpty():
                continue
            center = shape.centroid()
            if center.isNull():
                continue
            point = center.asPoint()
            owners.append((layer, feature.id()))
            centers.append((point.x(), point.y()))

    broken = outliers.outlier_indices(centers)
    doomed: dict = {}
    for index in broken:
        layer, fid = owners[index]
        doomed.setdefault(layer, []).append(fid)

    for layer, ids in doomed.items():
        layer.dataProvider().deleteFeatures(ids)

    for layer in buckets.values():
        layer.updateExtents()

    kept = [point for index, point in enumerate(centers) if index not in broken]
    return sum(len(ids) for ids in doomed.values()), outliers.view_bounds(kept)


def _new_memory_layer(cad_layer: str, suffix: str, wkb_name: str, crs_id: str, taken: set[str]):
    # 이름 짓는 규칙은 names.unique_name 하나만 쓴다. 여기서 루프를 따로 돌리면
    # 대소문자 처리 같은 수정이 한쪽에만 반영된다.
    name = names.unique_name(f"{names.sanitize(cad_layer)}_{suffix}", taken)

    return QgsVectorLayer(f"{wkb_name}?crs={crs_id}{_FIELDS}", name, "memory")


def _closed(ring):
    """첫 점을 끝에 붙인 닫힌 고리. 이미 닫혀 있으면 그대로."""
    ring = list(ring)
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _mesh_face(face) -> list[list[list[tuple[float, float, float]]]]:
    """면 하나를 곡면 위의 거의 평평한 조각들로. 조각 = [바깥 고리, 구멍 고리, …] (3D).

    tessellate 가 만든 (u,v) 영역을 격자로 자르고(GEOS) 칸마다 나온 다각형을 곡면
    point(u,v) 로 되돌린다. 평면은 자를 필요가 없고, 원기둥은 각도 방향만, 구·토러스는
    두 방향 다 자른다. 곡면을 못 읽은 면은 경계 고리를 그대로 평면으로 둔다.

    감김 방향은 곡면 법선(sense 를 곱한 것)과 맞춰 바깥을 향하게 한다 - 닫힌 솔리드의
    부피가 양수인지로 검증한다(tests).
    """
    from . import tessellate

    surf = face.surface
    regions = tessellate.face_regions(face) if surf is not None else []
    if not regions:
        # 곡면 정의가 없다(옛 SAT) - 경계만이라도 평면으로 그린다
        return [[list(ring) for ring in face.loops]] if face.loops else []

    outward = -1.0 if face.sense else 1.0
    pieces = []
    for region in regions:
        for uv_rings in _grid_pieces(surf, region):
            rings3 = [[surf.point(u, v) for (u, v) in ring] for ring in uv_rings]
            rings3 = [_dedupe(r) for r in rings3]
            if len(rings3[0]) < 3:
                continue
            uc = sum(p[0] for p in uv_rings[0]) / len(uv_rings[0])
            vc = sum(p[1] for p in uv_rings[0]) / len(uv_rings[0])
            want = surf.normal(uc, vc)
            have = _newell(rings3[0])
            if _length(have) < 1e-12:
                continue                          # 토러스 축 위처럼 한 점으로 모인 칸
            if (want[0] * have[0] + want[1] * have[1] + want[2] * have[2]) * outward < 0:
                rings3 = [list(reversed(r)) for r in rings3]
            pieces.append(rings3)
    return pieces


def _dedupe(ring):
    out = []
    for p in ring:
        if not out or any(abs(p[i] - out[-1][i]) > 1e-9 for i in range(3)):
            out.append(p)
    if len(out) > 1 and all(abs(out[0][i] - out[-1][i]) <= 1e-9 for i in range(3)):
        out.pop()
    return out


def _length(v):
    return (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5


def _newell(ring):
    nx = ny = nz = 0.0
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx, ny, nz)


def _grid_pieces(surf, region):
    """(u,v) 영역을 곡면이 정한 칸 크기로 자른다. 칸마다 [바깥, 구멍…] 고리를 준다."""
    outer = region[0]
    us = [p[0] for p in outer]
    vs = [p[1] for p in outer]
    du, dv = surf.steps(max(us) - min(us), max(vs) - min(vs))
    if du is None and dv is None:
        return [region]

    poly = QgsGeometry.fromPolygonXY([[QgsPointXY(u, v) for (u, v) in ring] for ring in region])
    if not poly.isGeosValid():
        poly = poly.makeValid()
    box = poly.boundingBox()
    if poly.isEmpty() or box.isNull() or not math.isfinite(box.width() + box.height()):
        return [region]
    u_lines = _lines(box.xMinimum(), box.xMaximum(), du)
    v_lines = _lines(box.yMinimum(), box.yMaximum(), dv)
    out = []
    for i in range(len(u_lines) - 1):
        for j in range(len(v_lines) - 1):
            cell = QgsGeometry.fromRect(QgsRectangle(u_lines[i], v_lines[j], u_lines[i + 1], v_lines[j + 1]))
            piece = poly.intersection(cell)
            # 영역 변이 격자선과 겹치면 두께 0 의 부스러기가 나온다 - 버린다
            if piece.isEmpty() or piece.area() < cell.area() * 1e-6:
                continue
            for part in piece.asGeometryCollection() if piece.isMultipart() else [piece]:
                if QgsWkbTypes.geometryType(part.wkbType()) != \
                        QgsWkbTypes.GeometryType.PolygonGeometry:
                    continue                      # 경계에 스친 선·점
                rings = part.asPolygon()
                if not rings or len(rings[0]) < 4:
                    continue
                out.append([[(pt.x(), pt.y()) for pt in ring[:-1]] for ring in rings])
    return out


def _lines(lo: float, hi: float, step) -> list[float]:
    """[lo, hi] 를 step 간격으로 자르는 선들. step 이 None 이면 자르지 않는다."""
    if step is None or step <= 0:
        return [lo - 1.0, hi + 1.0]
    start = math.floor(lo / step) * step
    lines = []
    x = start
    while x < hi + step:
        lines.append(x)
        x += step
    return lines


def _acis_layer(dxf: Path, crs_id: str, taken: set[str], acis_raw: Path | None = None,
                acds_raw: Path | None = None):
    """도면에 든 3차원 솔리드(ACIS)를 면 레이어로 만든다.

    OGR 은 3DSOLID·REGION 을 그리지 못한다. 그 안의 ACIS 데이터를 `acis` 모듈로 풀어
    면 폴리곤으로 낸다. 높이(Z)를 그대로 살린다 - 눌러 버리면 3차원 도면이 아니게 된다.

    도면에서 뽑아 둔 **원본** ACIS 를 먼저 본다. DXF 로 나온 SAT 는 LibreDWG 가 다시
    쓴 글자라 포인터가 어긋나 면이 뭉개지는데 원본은 그렇지 않다(acis 모듈의 SAB 설명).
    """
    from . import acis

    bodies = acis.sat_bodies(dxf)
    # 원본이 어디 있는지는 도면마다 다르다. R2013+ 라도 어떤 도면은 엔티티에,
    # 어떤 도면은 AcDs 절에만 있다. 한쪽이 비면 다른 쪽을 본다.
    #   entries 는 (핸들, 도면층, 색, 면 목록) 이고 면은 (조각들, 곡면 종류, 면 색) 다.
    entries: list[tuple[str, str, str, list]] = []
    raw = Path(acis_raw) if acis_raw is not None and Path(acis_raw).exists() else None
    acds = Path(acds_raw) if acds_raw is not None and Path(acds_raw).exists() else None
    sab = raw if raw is not None and acis.is_sab(raw) else acds
    if sab is not None:
        # 이진 ACIS - 곡면 정의까지 읽어 어떤 면이든 곡면 위에서 조각낸다.
        solids = acis.sab_bodies(sab)
        styles = acis.entity_styles(dxf, len(solids))
        for i, recs in enumerate(solids):
            handle, layer, color = styles[i]
            entries.append((handle, layer, color,
                            [(_mesh_face(face), face.kind, face.color) for face in acis.sab_face_topology(recs)]))
    elif raw is not None:
        # 글자 SAT(R14·R2000) - 곡면 정의가 없어 경계 고리를 평면으로 그린다.
        polys = acis.raw_faces(raw)
        if polys:
            # 원본 덩이는 솔리드별로 나뉘어 있지 않아 한 덩이로 들어온다. 도면 안
            # 모든 솔리드가 같은 도면층·색이면 그 값을 그대로 쓸 수 있지만, 섞여
            # 있으면 어느 면이 어느 것인지 가릴 길이 없다 - 그때는 비워 둔다.
            styles = {(layer, color) for _, layer, color in acis.entity_styles(dxf, len(bodies))}
            layer, color = styles.pop() if len(styles) == 1 else ("", "")
            entries = [(bodies[0][0] if bodies else "", layer, color,
                        [([rings], "", None) for rings in polys])]
    if not entries:
        # DXF 를 직접 읽는 길이다(무료판, 또는 원본을 못 받았을 때). 여기 SAT 는
        # AutoCAD 가 쓴 것이면 번호가 스스로 맞고, LibreDWG 가 다시 쓴 것이면 어긋난다.
        # 그래도 옛 걷기(종류로 짐작하는 것)보다 이쪽이 낫다 - 실측 13면/퇴화 4 에서
        # 13면/퇴화 0 이 됐다(2026-09-19, 실제 AutoCAD R2000 DXF).
        entries = [(handle, "", "",
                    [([rings], "", None) for rings in acis.raw_sat_faces("\n".join(lines))])
                   for handle, lines in bodies]
    if not any(e[3] for e in entries):
        return None

    layer = QgsVectorLayer(f"MultiPolygonZ?crs={crs_id}", "", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([
        QgsField("cad_handle", QVariant.String),  # 원본 엔티티 핸들
        QgsField("cad_type", QVariant.String),    # 3DSOLID / REGION …
        QgsField("solid", QVariant.String),       # 몇 번째 솔리드인가
        QgsField("face", QVariant.Int),          # 그 안에서 몇 번째 면인가
        QgsField("surface", QVariant.String),    # 평면/원뿔/토러스/…
        QgsField("layer", QVariant.String),      # 원본 도면층 이름
        QgsField("color", QVariant.String),      # 원본 색 (#rrggbb)
    ])
    layer.updateFields()

    features = []
    for index, (handle, cad_layer, cad_color, polygons) in enumerate(entries):
        for face_no, (pieces, kind, face_color) in enumerate(polygons, start=1):
            # 면 하나 = 곡면 위의 거의 평평한 조각 여럿. 조각마다 바깥 고리 + 구멍.
            # Z 를 살려야 하므로 XY 평면 함수가 아니라 QgsPolygon 을 직접 만든다.
            multi = QgsMultiPolygon()
            for rings in pieces:
                if not rings or len(rings[0]) < 3:
                    continue
                shape = QgsPolygon()
                shape.setExteriorRing(QgsLineString([QgsPoint(p[0], p[1], p[2]) for p in _closed(rings[0])]))
                for hole in rings[1:]:
                    if len(hole) >= 3:
                        shape.addInteriorRing(QgsLineString([QgsPoint(p[0], p[1], p[2]) for p in _closed(hole)]))
                multi.addGeometry(shape)
            geometry = QgsGeometry(multi)
            if geometry.isEmpty():
                continue
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            # 면에 따로 칠한 색이 있으면 그것이 엔티티 색을 이긴다(AutoCAD 규칙)
            feature.setAttributes([handle, "3DSOLID", f"ACIS{index + 1}", face_no, kind,
                                   cad_layer, face_color or cad_color])
            features.append(feature)
    if not features:
        return None
    provider.addFeatures(features)
    layer.setName(names.unique_name("EchoCad_3DSOLID_polygon", taken))
    return layer


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
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if first
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
        )
        error, message, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(gpkg), context, options
        )
        if error != QgsVectorFileWriter.WriterError.NoError:
            raise OSError(f"{layer.name()} — {tr('could not save')}: {message}")
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
