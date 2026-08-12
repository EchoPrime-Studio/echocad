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
    """
    keys = {
        "DXF_ENCODING": "UTF-8" if force_utf8 else None,
        "DXF_INLINE_BLOCKS": "TRUE" if inline_blocks else "FALSE",
    }
    previous = {key: gdal.GetConfigOption(key) for key in keys}
    for key, value in keys.items():
        gdal.SetConfigOption(key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            gdal.SetConfigOption(key, value)
