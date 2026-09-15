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
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import Qt
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


def import_dwg(
    dwg: Path,
    output_gpkg: Path | None = None,
    crs: str | None = None,
    exe: Path | None = None,
    feedback=None,
    extract_attribs: bool = True,
    join_text: bool = False,
    close_tolerance: float = 0.0,
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
            (tr("polygon closing"), bool(close_tolerance)),
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
                + " " + tr("Already bought Pro? Get your installer at "
                           "https://echocad.pages.dev/key"),
                elapsed_sec=time.monotonic() - started), []
        else:
            converted = engine.convert(dwg, work / (dwg.stem + ".dxf"), exe=exe)
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
            skipped: list[int] = [0]
            layers = _split_by_cad_layer(source, crs_id, feedback, profile, unmatched,
                                         keep_z=keep_z, hidden=hidden, skipped=skipped,
                                         utf8=utf8)

        if layers is None:
            return ImportResult(dwg.name, "error", tr("Could not read the DXF"),
                                elapsed_sec=time.monotonic() - started), []

        dropped, view = _drop_broken_geometries(layers)

        # 거의 닫힌 선을 면으로. 문자 붙이기보다 먼저 해야 새로 생긴 면도 문자를 받는다.
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
        if georef is not None and pro_georef is not None and pro.unlocked():
            pro_georef.apply(layers, georef, feedback=feedback)

        _apply_cad_colors(layers)
        _enable_text_labels(layers)
        absent_refs = xrefs.missing(source, dwg)

        block_layers = []
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

    finally:
        shutil.rmtree(work, ignore_errors=True)

    if feedback is not None and feedback.isCanceled():
        return ImportResult(dwg.name, "error", tr("Cancelled"),
                            elapsed_sec=time.monotonic() - started), []

    if not layers:
        return ImportResult(dwg.name, "empty", tr("No entities"),
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
            tr("Dropped {count} features with broken coordinates").format(count=dropped)
        if dropped else "",
            tr("Block attributes were not extracted - the licence is not active")
        if attribs_skipped else "",
            tr("Closed {count} open polylines into polygons").format(count=closed_rings)
        if closed_rings else "",
            tr("Attached {count} texts to shapes").format(count=joined) if joined else "",
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
    ), produced


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
        copied["text_dx"] = style.dx
        copied["text_dy"] = style.dy
        if not target.dataProvider().addFeature(copied):
            # 넣기가 실패하면 그 도형은 사라진다. 세지 않으면 아무도 모른다 -
            # 위의 Z 문제를 이 숫자가 없어서 오래 못 봤다.
            if skipped is not None:
                skipped[0] += 1


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
        properties.setProperty(QgsPalLayerSettings.Property.LabelRotation, QgsProperty.fromField("text_angle"))
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
