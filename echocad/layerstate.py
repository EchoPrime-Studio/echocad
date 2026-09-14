# CAD 레이어의 켜짐·얼림 상태를 DXF 표에서 읽음. 안 보이게 해 둔 레이어를 거를 때 씀
#
# 왜 필요한가 -
# 제도자가 안 보이게 해 둔 레이어까지 가져오면 QGIS 레이어 목록이 쓸데없이 길어진다.
# 표본 57장에서 레이어 868개 중 68개(7.8%)가 꺼져 있거나 얼어 있었다 (2026-08-19 실측).
#
# 왜 기본으로 거르지 않는가 -
# 안 보이게 해 둔 것이 곧 필요 없다는 뜻은 아니다. 잠깐 가려 둔 것일 수도 있고, 납품
# 도면에서 그 레이어에만 정보가 있는 경우도 있다. 데이터를 조용히 잃는 것보다 목록이
# 긴 편이 낫다. 거를지는 사용자가 정한다.
#
# 왜 줄 번호로 짝을 짓지 않는가 -
# DXF 는 그룹코드와 값이 한 줄씩 번갈아 나오지만 "짝수 줄이 코드" 라고 가정하면 안 된다.
# LibreDWG 가 헤더에 쓰는 값에 줄바꿈이 섞이면 그 뒤로 정렬이 한 줄 밀린다. 실제로
# acadsharp-r2004.dxf 와 acadsharp-geoloc.dxf 가 그래서 꺼진 레이어를 하나도 못 찾았다
# (2026-08-20 실측). 코드/값을 번갈아 읽는 상태기계로 훑으면 밀려도 상관없다.
#
# DXF 규격 -
#   TABLES 구역의 LAYER 항목에서
#     코드 2  레이어 이름
#     코드 62 색. 음수면 꺼진 것 (AutoCAD 가 색을 음수로 뒤집어 표시한다)
#     코드 70 플래그. 1 비트가 서면 얼린 것
from __future__ import annotations

from pathlib import Path

_FROZEN_BIT = 1


def hidden_layers(dxf: Path, utf8: bool = True) -> set[str]:
    """꺼져 있거나 얼려 있는 레이어 이름. 읽지 못하면 빈 집합.

    utf8 은 도면 본문의 인코딩이다. OGR 이 CP949 로 읽는 도면을 여기서 UTF-8 로 읽으면
    한글 레이어 이름이 깨져 엔티티의 레이어명과 영영 안 맞는다 - 거르기가 조용히 무효가
    된다. 부르는 쪽이 dxfenc.content_is_utf8() 로 정한 값을 그대로 넘겨야 한다.

    여기서 예외가 나면 가져오기가 통째로 죽는다. 거르기는 부가 기능일 뿐이므로
    무슨 일이 있어도 집합 하나를 돌려준다.
    """
    hidden: set[str] = set()
    in_layer = False
    name, color, flags = "", 0, 0

    def flush():
        nonlocal name, color, flags
        if in_layer and name and (color < 0 or flags & _FROZEN_BIT):
            hidden.add(name)
        name, color, flags = "", 0, 0

    encoding = "utf-8" if utf8 else "cp949"
    try:
        # 파일 전체를 메모리에 올리지 않는다. 표본 중 28MB 짜리가 있고, 필요한 것은
        # 앞쪽 TABLES 구역뿐이라 거기서 끊는다.
        with Path(dxf).open("r", encoding=encoding, errors="replace") as handle:
            code = None
            seen_table = False
            for raw in handle:
                value = raw.strip()
                if code is None:
                    code = value
                    continue
                group, code = code, None

                if group == "0":
                    flush()
                    in_layer = value == "LAYER"
                    if in_layer:
                        seen_table = True
                    elif value == "ENDSEC" and seen_table:
                        break      # TABLES 가 끝났다. ENTITIES 까지 훑을 이유가 없다
                elif in_layer and group == "2":
                    name = value
                elif in_layer and group == "62":
                    try:
                        color = int(value)
                    except ValueError:
                        color = 0
                elif in_layer and group == "70":
                    try:
                        flags = int(value)
                    except ValueError:
                        flags = 0
    except OSError:
        return set()

    flush()
    return hidden
