# 도면 안 표(TABLE)의 내용을 찾아낸다. 변환기가 표를 놓는 지시를 못 써서 그냥 두면 사라짐
#
# 무슨 일이 일어나나 -
# DWG 의 표는 ACAD_TABLE 이라는 엔티티다. LibreDWG 는 이것을 못 읽어 "알 수 없는
# 엔티티" 로 둔다. 표의 내용이 담긴 `*T` 블록은 제대로 써 내는데, **그 블록을 도면에
# 놓으라는 지시**가 빠진다. 놓는 것이 없으니 GDAL 은 그릴 것이 없다고 보고,
# 사용자에게는 표가 통째로 사라진다 (2026-08-26 실측 - 표 글자 25개가 없어졌다).
#
# 왜 그냥 가져오면 되나 -
# `*T` 블록 안의 엔티티는 이미 도면 좌표를 갖고 있다. 우리 파일 안의 좌표와 다른
# 해독기가 놓은 자리가 소수점까지 같았다. 그러니 그대로 가져오면 제자리에 놓인다.
#
# 왜 `*T` 만인가 -
# 놓이지 않은 블록은 원래 도면에 안 보이는 것이 맞다. 표만 예외다 - 놓는 지시를
# 우리가 못 읽어서 생긴 일이기 때문이다. 범위를 넓히면 안 보여야 할 것이 나온다.
#
# 표가 또 다른 블록을 부를 때 -
# 표 안에 블록이 들어가는 경우가 있다. 그 블록도 따라가야 글자를 안 잃는다. 다만
# **원점에 배율·회전 없이 놓인 것만** 따라간다. 위치나 배율이 붙은 것을 그대로 담으면
# 좌표를 옮겨 주지 않아 엉뚱한 자리에 그려진다 - 없는 것보다 나쁘다.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 가져올 블록의 이름 앞머리.
#   *T  표. ACAD_TABLE 엔티티가 놓는데 그 엔티티를 변환기가 못 읽어 놓이지 않는다.
#   *D  치수. DIMENSION 엔티티가 놓고 GDAL 이 그려 주기는 하는데 **글자를 자주 뺀다**
#       (2026-08-27 실측 - fire-station 의 치수 글자 6개 중 1개만 들어왔다).
#       겹치는 것은 담는 쪽에서 걸러 낸다.
_WANTED_BLOCKS = ("*T", "*D")

# 좌표·배율을 이만큼까지는 같은 것으로 본다. DXF 는 실수를 글자로 적어 정확히 0 이
# 아닌 값이 나올 수 있다.
_EPSILON = 1e-9


# 치수 블록에서는 글자만 가져온다. 치수선·화살표는 GDAL 이 DIMENSION 엔티티를 보고
# 이미 그리므로, 통째로 담으면 같은 선이 두 번 그려진다.
_TEXT_ONLY = "*D"
_TEXT_ENTITIES = ("TEXT", "MTEXT", "ATTRIB")


@dataclass
class _Block:
    """블록 하나에서 우리가 쓸 것 - 안에 든 엔티티 핸들과, 안에서 부르는 블록들."""

    handles: set[str] = field(default_factory=set)
    texts: set[str] = field(default_factory=set)
    calls: list[tuple[str, bool]] = field(default_factory=list)  # (블록 이름, 그대로 놓였나)


def _plain(insert: dict) -> bool:
    """원점에 배율·회전 없이 놓였는가. 그때만 좌표를 그대로 써도 된다."""
    for key, default in (("10", 0.0), ("20", 0.0), ("30", 0.0), ("50", 0.0)):
        if abs(insert.get(key, default)) > _EPSILON:
            return False
    for key in ("41", "42", "43"):
        if abs(insert.get(key, 1.0) - 1.0) > _EPSILON:
            return False
    return True


def read(dxf: Path) -> set[str]:
    """도면에 놓이지 않은 표 블록 안 엔티티들의 핸들.

    핸들로 돌려주는 것은 GDAL 이 이 블록들의 이름을 빈 문자열로 주기 때문이다.
    이름으로는 고를 수가 없어서 핸들로 고른다 (2026-08-26 실측).
    """
    blocks: dict[str, _Block] = {}
    placed: set[str] = set()          # ENTITIES 에서 직접 부르는 블록
    section: str | None = None
    block: str | None = None
    entity: str | None = None
    seen_handle = False
    insert: dict | None = None
    insert_name: str | None = None

    def close_insert() -> None:
        nonlocal insert, insert_name
        if insert is not None and insert_name:
            if block and block in blocks:
                blocks[block].calls.append((insert_name, _plain(insert)))
            elif section == "ENTITIES":
                placed.add(insert_name)
        insert, insert_name = None, None

    def number(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    with Path(dxf).open("r", encoding="utf-8", errors="replace") as stream:
        code: str | None = None
        for line in stream:
            value = line.strip()
            if code is None:
                code = value
                continue
            group, code = code, None

            if group == "0":
                close_insert()
                if value == "SECTION":
                    section = "?"
                elif value == "BLOCK":
                    block = "?"
                elif value == "ENDBLK":
                    block = None
                entity = value
                seen_handle = False
                if value == "INSERT":
                    insert = {}
                continue

            if group == "2":
                if section == "?":
                    section = value
                elif block == "?":
                    block = value
                    blocks.setdefault(block, _Block())
                elif insert is not None:
                    insert_name = value
                continue

            if insert is not None and group in ("10", "20", "30", "41", "42", "43", "50"):
                parsed = number(value)
                if parsed is not None:
                    insert[group] = parsed
                continue

            # 핸들은 엔티티마다 처음 나오는 5 다. BLOCK 자체의 핸들은 담지 않는다 -
            # 담아 봐야 GDAL 이 내주는 피처와 짝이 없다.
            if (group == "5" and not seen_handle and block
                    and block in blocks and entity not in (None, "BLOCK", "ENDBLK")):
                blocks[block].handles.add(value)
                if entity in _TEXT_ENTITIES:
                    blocks[block].texts.add(value)
                seen_handle = True
    close_insert()

    # 표 블록에서 시작해, 그대로 놓인 것만 따라간다. 도면에서 이미 닿는 블록은
    # GDAL 이 알아서 펼치므로 건드리지 않는다 - 담으면 같은 것이 두 번 그려진다.
    wanted: set[str] = set()
    todo = [name for name in blocks if name.startswith(_WANTED_BLOCKS)]
    while todo:
        name = todo.pop()
        if name in wanted or name in placed or name not in blocks:
            continue
        wanted.add(name)
        todo.extend(inner for inner, plain in blocks[name].calls if plain)

    handles: set[str] = set()
    for name in wanted:
        block = blocks[name]
        handles |= block.texts if name.startswith(_TEXT_ONLY) else block.handles
    return handles
