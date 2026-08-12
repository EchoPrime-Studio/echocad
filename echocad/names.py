# CAD 이름을 GIS에서 쓸 수 있는 이름으로 바꾸는 순수 함수. QGIS에 의존하지 않아 단독 테스트된다
from __future__ import annotations

import re

# GeoPackage는 유니코드 이름을 허용하므로 글자는 스크립트를 가리지 않고 지킨다.
# \w는 파이썬 3에서 기본이 유니코드라 한글·악센트 라틴·키릴이 모두 살아남는다
# (초안의 ASCII+한글 화이트리스트는 réseaux를 r_seaux로 깎아 먹었다).
_INVALID = re.compile(r"\W+", re.UNICODE)


# OGR DXF 드라이버가 주는 SubClasses에서 글자 엔티티를 가리는 표식.
# SOLID 같은 채움 엔티티도 Text 필드가 채워져 오므로 이걸로 걸러야 한다.
_TEXT_SUBCLASSES = ("AcDbText", "AcDbMText")


def is_text_entity(subclasses: str) -> bool:
    """DXF SubClasses 문자열이 TEXT·MTEXT 계열인지."""
    return any(marker in (subclasses or "") for marker in _TEXT_SUBCLASSES)


def sanitize(name: str, fallback: str = "layer") -> str:
    """CAD 레이어명·속성 태그를 레이어명/필드명으로 쓸 수 있게 정리한다."""
    cleaned = _INVALID.sub("_", name or "").strip("_")
    if not cleaned:
        return fallback
    if cleaned[0].isdigit():
        cleaned = "f_" + cleaned
    return cleaned


def unique_name(base: str, taken: set[str]) -> str:
    """`taken`에 없는 이름을 만들어 등록하고 돌려준다.

    레이어명이 겹치면 GeoPackage에 쓸 때 뒤엣것이 앞엣것을 덮어써 조용히 데이터를
    잃는다. 이름을 짓는 곳마다 같은 규칙을 쓰도록 여기 하나로 모았다.
    """
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


def make_unique(names: list[str], fallback: str = "layer") -> dict[str, str]:
    """원본 이름 → 정리된 고유 이름 매핑. 입력 순서대로 _2, _3을 붙여 충돌을 푼다.

    같은 원본이 두 번 들어오면 같은 결과를 돌려준다. 정리 후 겹치는 서로 다른
    원본만 접미를 받는다 — 접미 없이 두면 서로 다른 레이어가 조용히 합쳐진다.
    """
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for original in names:
        if original in mapping:
            continue
        base = sanitize(original, fallback)
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}_{n}"
        used.add(candidate)
        mapping[original] = candidate
    return mapping
