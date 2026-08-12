# DXF 내용의 실제 인코딩을 판별한다. 헤더의 $DWGCODEPAGE 선언과 다를 수 있다
from __future__ import annotations

import codecs
from pathlib import Path

_CHUNK = 1 << 20


def content_is_utf8(dxf: Path) -> bool:
    """파일 전체가 UTF-8로 디코딩되는가.

    `dwg2dxf`는 도면에 따라 내용을 UTF-8로 쓰면서 `$DWGCODEPAGE`는 원본 DWG의
    지역 코드페이지(ANSI_1252, ANSI_949 …)를 그대로 남긴다. 그럴 때 헤더를 믿으면
    `grün`이 `grÃ¼n`이 된다.

    반대 경우도 실재한다 — 선언도 CP949이고 내용도 진짜 CP949인 도면이 있다.
    그때 UTF-8을 강제하면 한글이 통째로 깨진다. 그래서 이름이나 헤더가 아니라
    **바이트를 직접 보고** 판단한다.

    전체를 읽되 메모리에 통째로 올리지 않으려고 조각내어 증분 디코딩한다.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with Path(dxf).open("rb") as handle:
            while True:
                block = handle.read(_CHUNK)
                if not block:
                    decoder.decode(b"", final=True)
                    return True
                decoder.decode(block)
    except UnicodeDecodeError:
        return False
    except OSError:
        # 읽지 못하면 GDAL 판단에 맡긴다.
        return False
