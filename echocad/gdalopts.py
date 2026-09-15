# DXF를 읽는 동안만 GDAL 설정을 바꾸고 원복하는 컨텍스트 매니저. importer와 pro 양쪽이 쓴다
from __future__ import annotations

from contextlib import contextmanager

from osgeo import gdal


@contextmanager
def dxf_options(inline_blocks: bool = True, force_utf8: bool = False):
    """OGR의 DXF 읽기 방식을 이 블록 안에서만 바꾼다.

    - `DXF_ENCODING` — **내용이 실제로 UTF-8일 때만** UTF-8을 강제한다. dwg2dxf가
      내용은 UTF-8로 쓰면서 `$DWGCODEPAGE`는 원본 DWG의 지역 코드페이지를 남기는
      도면이 있어서다(그때 헤더를 믿으면 `grün`이 `grÃ¼n`이 된다). 내용이 진짜
      CP949인 도면도 실재하므로, 그때는 손대지 않고 GDAL이 헤더대로 읽게 둔다.
      판별은 `dxfenc.content_is_utf8`이 바이트를 보고 한다.
    - `DXF_INLINE_BLOCKS` — TRUE면 블록을 펼쳐 도형을 얻고, FALSE면 블록 참조를 점
      하나로 주면서 `BlockAttributes`(속성 태그·값)를 함께 준다.

    GDAL 설정은 전역이라 반드시 원복한다. OGR은 피처를 지연 읽기하므로 순회가
    끝날 때까지 블록 안에 있어야 한다.

    읽는 동안 GDAL 오류 핸들러도 우리 것으로 바꾼다. QGIS 4 는 기본 핸들러가
    CE_Failure 를 **모달 오류창**으로 띄운다 — CAD 도면은 닫히지 않은 폴리라인 같은
    것이 흔해 경고가 수백 건씩 나오므로, 그대로 두면 사용자가 OK 를 계속 눌러야 하고
    창에는 QGIS 빌드머신 내부 경로가 그대로 보인다 (2026-08-22 QGIS 4.2.1 실측).
    QGIS 3.44 는 로그로만 보내서 이 문제가 안 보였다. 삼키지 않고 로그로 넘긴다.
    """
    keys = {
        "DXF_ENCODING": "UTF-8" if force_utf8 else None,
        "DXF_INLINE_BLOCKS": "TRUE" if inline_blocks else "FALSE",
        # 블록 안 도형을 하나로 뭉치지 않는다. GDAL 기본은 뭉치는 것이라, 창문·문·
        # 가구처럼 블록으로 만든 것이 **통째로 점 하나**가 된다. 도면 내용의 대부분이
        # 블록에 들어 있으므로 그대로 두면 거의 다 잃는다 - floorplan-elevation 은
        # 원본에 그릴 것이 20,439 개인데 1,113 개만 들어왔다 (2026-08-26 실측).
        # 끄면 9,910 개가 된다. 뭉친 것은 레이어·색·선종류도 하나로 뭉개져,
        # CAD 레이어별로 가르는 우리 방식과 애초에 맞지 않는다.
        "DXF_MERGE_BLOCK_GEOMETRIES": "FALSE" if inline_blocks else None,
    }
    previous = {key: gdal.GetConfigOption(key) for key in keys}
    for key, value in keys.items():
        gdal.SetConfigOption(key, value)
    gdal.PushErrorHandler(_log_quietly)
    try:
        yield
    finally:
        gdal.PopErrorHandler()
        for key, value in previous.items():
            gdal.SetConfigOption(key, value)


def _log_quietly(err_class, err_no, message):
    """GDAL 이 부르는 오류 핸들러. 창을 띄우지 않고 QGIS 로그 창에만 남긴다."""
    try:
        from qgis.core import Qgis, QgsMessageLog
    except ImportError:      # QGIS 밖(도구·테스트)에서는 조용히 넘어간다
        return
    level = Qgis.MessageLevel.Critical if err_class == gdal.CE_Failure else Qgis.MessageLevel.Warning
    QgsMessageLog.logMessage(f"GDAL {err_no}: {message}", "EchoCad", level)
