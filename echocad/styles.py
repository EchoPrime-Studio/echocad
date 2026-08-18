# OGR 스타일 문자열에서 CAD 색·글자 크기·각도를 뽑는 파서. 원본 도면과 같은 모양을 내려면 이게 필요하다
from __future__ import annotations

import re
from dataclasses import dataclass

# OGR DXF 드라이버가 주는 형식 예시
#   PEN(c:#bababa)
#   BRUSH(fc:#7f7f7f)
#   LABEL(f:"Arial",t:"Ausziehtreppe",a:90,s:125g,p:7,c:#bababa)
#
# 본문에서 괄호는 따옴표 안에만 올 수 있다. 글자 내용에 괄호가 들어가는 일이
# 흔하므로(`(주)`, `(단위:m)`) 단순히 첫 `)`에서 끊으면 뒤따르는 크기·색을 통째로 잃는다.
_TOOL = re.compile(r'(\w+)\(((?:[^()"]|"(?:[^"\\]|\\.)*")*)\)')
# 값에 따옴표가 있으면 그 안의 쉼표는 구분자가 아니다.
_PARAM = re.compile(r'(\w+):("(?:[^"\\]|\\.)*"|[^,]*)')

DEFAULT_COLOR = "#000000"


# OGR LABEL의 앵커(p:1~9)를 QGIS 라벨 사분면 값으로 옮긴다.
# OGR은 글자의 어느 지점이 삽입점인지를, QGIS는 삽입점 기준 어느 쪽에 글자를 둘지를 말한다.
# 예: p1(왼쪽 아래가 삽입점) → 글자는 오른쪽 위로 뻗는다 → AboveRight(2).
_QUADRANT = {1: 2, 2: 1, 3: 0, 4: 5, 5: 4, 6: 3, 7: 8, 8: 7, 9: 6}
DEFAULT_QUADRANT = 2


@dataclass
class Style:
    stroke: str | None = None   # 선 색 (#rrggbb)
    width: float = 0.0          # 선 폭. 밀리미터(출력 폭). 0이면 지정 없음(가는 선)
    fill: str | None = None     # 채움 색
    label_color: str | None = None
    size: float | None = None   # 글자 높이. 도면 단위
    angle: float = 0.0          # 글자 회전. 도(°)
    quadrant: int = DEFAULT_QUADRANT  # 삽입점 기준 글자 방향
    dx: float = 0.0             # 글자 오프셋. 도면 단위
    dy: float = 0.0

    @property
    def color(self) -> str:
        """이 피처를 그릴 대표 색. 선 → 채움 → 글자 순으로 있는 것을 쓴다."""
        return self.stroke or self.fill or self.label_color or DEFAULT_COLOR


def _hex6(value: str) -> str | None:
    """OGR 색은 #rrggbb 또는 알파가 붙은 #rrggbbaa로 온다. 앞 6자리만 쓴다."""
    value = value.strip()
    if not value.startswith("#") or len(value) < 7:
        return None
    return value[:7].lower()


def _number(value: str) -> float | None:
    # 크기는 "125g"(도면 단위)나 "12pt"처럼 단위가 붙어 온다.
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def parse(style_string: str) -> Style:
    """OGR 스타일 문자열을 Style로. 못 읽는 부분은 조용히 건너뛴다."""
    style = Style()
    for tool, body in _TOOL.findall(style_string or ""):
        params = {
            key: value.strip('"')
            for key, value in _PARAM.findall(body)
        }
        if tool == "PEN":
            style.stroke = _hex6(params.get("c", "")) or style.stroke
            # 실측 — 실무 도면 320개 중 306개가 선폭을 갖는다(0.18·0.4·0.7 도면단위).
            width = _number(params.get("w", ""))
            if width and width > 0:
                style.width = width
        elif tool == "BRUSH":
            style.fill = _hex6(params.get("fc", "")) or style.fill
        elif tool == "LABEL":
            style.label_color = _hex6(params.get("c", "")) or style.label_color
            size = _number(params.get("s", ""))
            if size and size > 0:
                style.size = size
            angle = _number(params.get("a", ""))
            if angle is not None:
                style.angle = angle
            anchor = _number(params.get("p", ""))
            if anchor is not None:
                style.quadrant = _QUADRANT.get(int(anchor), DEFAULT_QUADRANT)
            for axis in ("dx", "dy"):
                offset = _number(params.get(axis, ""))
                if offset is not None:
                    setattr(style, axis, offset)
    return style
