# DXF의 LTYPE·LAYER 테이블을 읽어 선종류를 판별한다. 원본의 파선·쇄선이 실선으로 나오면 도면이 달라 보인다
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Qt 펜 스타일 이름. QGIS의 stroke_style 표현식이 그대로 받는다.
SOLID = "solid"
DASH = "dash"
DOT = "dot"
DASH_DOT = "dash dot"
DASH_DOT_DOT = "dash dot dot"

# 엔티티가 자기 선종류를 갖지 않고 레이어·블록을 따른다는 표시.
_INHERIT = {"", "bylayer", "byblock"}


def classify(pattern: list[float]) -> str:
    """LTYPE 대시 길이 목록을 Qt 펜 스타일로 분류한다.

    DXF는 양수를 그리는 구간, 음수를 띄우는 구간, 0을 점으로 쓴다. 이름
    (`STRICHLINIE2`, `MITTE`, `HIDDEN`, `CENTER` …)은 도면·언어마다 달라 못 믿으므로
    실제 패턴 모양으로 판단한다.
    """
    if not any(value < 0 for value in pattern):
        return SOLID

    drawn = [value for value in pattern if value >= 0]
    dots = sum(1 for value in drawn if value == 0)
    dashes = sum(1 for value in drawn if value > 0)

    if dashes == 0:
        return DOT
    if dots == 0:
        return DASH
    if dots >= 2:
        return DASH_DOT_DOT
    return DASH_DOT


@dataclass
class LinetypeTable:
    """선종류 이름 → 펜 스타일, 그리고 레이어 → 선종류 이름."""

    patterns: dict[str, str] = field(default_factory=dict)
    by_layer: dict[str, str] = field(default_factory=dict)

    def style_of(self, entity_linetype: str | None, cad_layer: str | None) -> str:
        """이 피처를 그릴 펜 스타일.

        엔티티가 선종류를 직접 갖고 있으면 그것을, 없거나 ByLayer면 레이어 것을 쓴다.
        실무 도면은 대부분 레이어에만 지정하므로 이 폴백이 없으면 파선이 전부 실선이 된다.
        """
        name = (entity_linetype or "").strip()
        if name.lower() in _INHERIT:
            name = self.by_layer.get(cad_layer or "", "")
        return self.patterns.get(name, SOLID)


def read(dxf: Path) -> LinetypeTable:
    """DXF의 TABLES 섹션에서 LTYPE 패턴과 레이어별 선종류를 한 번에 읽는다.

    그룹코드 — 2는 이름, 49는 대시 구간 길이, 6은 레이어의 선종류다.
    """
    table = LinetypeTable()
    kind: str | None = None
    name: str | None = None
    linetype: str | None = None
    pattern: list[float] = []

    def flush():
        nonlocal name, linetype, pattern
        if kind == "LTYPE" and name:
            table.patterns[name] = classify(pattern)
        elif kind == "LAYER" and name and linetype:
            table.by_layer[name] = linetype
        name, linetype, pattern = None, None, []

    with Path(dxf).open("r", encoding="utf-8", errors="replace") as handle:
        code = None
        for raw in handle:
            value = raw.strip()
            if code is None:
                code = value
                continue
            group, code = code, None

            if group == "0":
                flush()
                kind = value if value in ("LTYPE", "LAYER") else None
                if value == "ENDSEC" and (table.patterns or table.by_layer):
                    break  # TABLES가 끝났다. ENTITIES까지 훑을 이유가 없다
            elif kind and group == "2":
                name = value
            elif kind == "LTYPE" and group == "49":
                try:
                    pattern.append(float(value))
                except ValueError:
                    pass
            elif kind == "LAYER" and group == "6":
                linetype = value

    flush()
    return table
