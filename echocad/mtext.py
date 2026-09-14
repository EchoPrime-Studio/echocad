# DXF의 MTEXT 원문을 직접 읽어 서식 코드를 푼다. OGR이 쌓은 분수를 뭉개기 때문
#
# 왜 직접 읽나 -
# OGR DXF 드라이버는 MTEXT 의 서식 코드를 스스로 벗기는데, 쌓은 분수에서 뜻을 바꾼다.
# `4'-0\S1/4;"` (4피트 0과 1/4인치)가 `4'-014` 로 나온다 (2026-08-26 실측).
# 치수 도면에서 이러면 사용자가 틀린 줄도 모르고 쓴다. OGR 은 원문을 돌려줄 방법이
# 없다 - RawCodeValues 를 켜도 코드 1 은 이미 소비돼 빠져 있다.
#
# 어디까지만 손대나 -
# 서식 코드가 든 MTEXT 만 우리 값으로 바꾼다. 코드가 없는 글자는 OGR 값을 그대로 둔다.
# 잘 되던 것까지 우리 해석으로 갈아엎지 않으려는 것이다.
from __future__ import annotations

import re
from pathlib import Path

# 서식 코드가 들어 있는지 보는 표시. 이게 없으면 OGR 값과 다를 이유가 없다.
_HAS_CODE = re.compile(r"[\\{}]")

# 쌓은 글자 - `\S윗값<구분><아랫값>;`. 구분은 / # ^ 셋이다.
#   /  가로줄 분수      1/4
#   #  빗금 분수        1/4
#   ^  줄 없이 위아래   공차 표기
_STACK = re.compile(r"\\S([^;\\]*?)([/#^])([^;\\]*?);")

# `\H1.5x;` `\C3;` `\fArial|b0;` 처럼 값이 붙고 세미콜론으로 끝나는 코드들.
# U 는 여기 넣지 않는다 - `\U+00B0` 는 설정이 아니라 글자 하나다.
_SETTING = re.compile(r"\\[HWTQAfFCcLlOoKkpX][^;\\]*;", re.IGNORECASE)

# 유니코드 글자 표기. `\U+00B0` 는 도(°), `\U+2205` 는 지름(∅) 이다. 안 풀면 도면에
# `94+00B0` 같은 것이 그대로 찍힌다 (2026-08-27 실측).
_UNICODE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def decode(raw: str) -> str:
    """MTEXT 원문에서 서식 코드를 걷어내고 읽을 수 있는 글자를 돌려준다."""
    if not raw:
        return ""

    def unstack(match: re.Match) -> str:
        upper, sep, lower = match.group(1), match.group(2), match.group(3)
        body = f"{upper}/{lower}" if sep in "/#" else f"{upper} {lower}"
        # 쌓아서 보이던 것을 한 줄로 펴면 앞 글자와 붙는다. `4'-0\\S1/4;` 가
        # `4'-01/4` 가 되어 "4피트 1/4" 처럼 읽힌다. 붙을 때만 한 칸 띄운다.
        before = match.string[:match.start()]
        return (" " if before and before[-1].isalnum() else "") + body

    # 순서가 중요하다. 쌓은 글자를 먼저 살려야 그 안의 값이 다른 규칙에 안 먹힌다.
    text = _STACK.sub(unstack, raw)
    text = _SETTING.sub("", text)
    # 글자 표기는 설정을 걷어낸 뒤에 푼다. 먼저 풀면 나온 글자가 설정 규칙에 걸린다.
    text = _UNICODE.sub(lambda m: chr(int(m.group(1), 16)), text)

    # 남은 것들. 이스케이프된 글자는 살리고 나머지 표시는 지운다.
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            if nxt in "P":
                out.append("\n")
            elif nxt == "~":
                out.append(" ")
            elif nxt in "\\{}":
                out.append(nxt)
            # 알 수 없는 코드는 표시만 버리고 글자는 남긴다
            index += 2
            continue
        if char in "{}":
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def shape_key(text: str) -> str:
    """서식 코드를 어떻게 벗기든 남는 뼈대. 글자와 숫자만 남겨 소문자로 만든다.

    핸들만으로는 못 찾기 때문에 있다. 블록 안의 글자를 OGR 이 펼칠 때 원래 엔티티가
    아니라 **블록을 놓은 INSERT 의 핸들**을 붙인다. 치수 글자가 전부 여기 해당한다
    (2026-08-26 실측 - 우리 662, OGR 527).

    뼈대는 양쪽이 같다. `4'-0 1/4"` 도 `4'-014` 도 `4014` 가 된다.
    """
    return "".join(c for c in str(text or "") if c.isalnum()).lower()


class Table:
    """푼 글자를 핸들과 글자 뼈대 두 가지로 찾을 수 있게 담아 둔다."""

    def __init__(self, by_handle: dict[str, str]):
        self.by_handle = by_handle
        # 뼈대가 겹치면 어느 쪽인지 알 수 없다. 겹치는 것은 아예 빼서 엉뚱한 글자로
        # 바꾸는 일이 없게 한다.
        counts: dict[str, int] = {}
        for value in by_handle.values():
            counts[shape_key(value)] = counts.get(shape_key(value), 0) + 1
        self.by_shape = {shape_key(v): v for v in by_handle.values()
                         if counts[shape_key(v)] == 1}

    def __len__(self) -> int:
        return len(self.by_handle)

    def __bool__(self) -> bool:
        return bool(self.by_handle)

    def resolve(self, handle: str, ogr_text: str) -> str | None:
        """이 엔티티에 쓸 우리 값. 없으면 None 이고, 그러면 OGR 값을 그대로 쓴다."""
        ours = self.by_handle.get(str(handle or ""))
        if ours:
            return ours
        return self.by_shape.get(shape_key(ogr_text))


def read(dxf: Path) -> Table:
    """서식 코드가 든 MTEXT 를 읽어 푼 것들.

    파일을 끝까지 읽는다. BLOCKS 의 첫 ENDSEC 에서 멈추면 ENTITIES 의 글자를 통째로
    놓친다 - hatches 에서 이미 겪었다.
    """
    result: dict[str, str] = {}
    handle: str | None = None
    chunks: list[str] = []
    inside = False

    def flush() -> None:
        nonlocal handle, chunks, inside
        if inside and handle:
            raw = "".join(chunks)
            if _HAS_CODE.search(raw):
                result[handle] = decode(raw)
        handle, chunks, inside = None, [], False

    with Path(dxf).open("r", encoding="utf-8", errors="replace") as stream:
        code: str | None = None
        for line in stream:
            value = line.rstrip("\r\n")
            if code is None:
                code = value.strip()
                continue
            group, code = code, None

            if group == "0":
                flush()
                inside = value.strip() == "MTEXT"
                continue
            if not inside:
                continue
            if group == "5" and handle is None:
                handle = value.strip()
            elif group == "3":
                # 긴 글자는 250 자씩 잘려 3 으로 여러 번 오고 마지막만 1 로 온다.
                chunks.append(value)
            elif group == "1":
                chunks.append(value)
    flush()
    return Table(result)
