# DXF의 HATCH 엔티티에서 채움 패턴을 읽는다. OGR은 채움 색만 주므로 여기서 직접 읽어야 한다
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

# 패턴 정의가 파일 안에 들어 있어서 AutoCAD의 .pat 라이브러리가 필요 없다.
# 각 패턴은 평행선 가족 여러 개로 이뤄지고, 가족마다 각도·기준점·오프셋·대시가 있다.
#
# 그룹코드 — 5 핸들, 2 패턴명, 70 솔리드 여부, 52 전체 회전각, 41 축척,
#            78 가족 수, 53 가족 각도, 43/44 기준점, 45/46 오프셋, 79 대시 수, 49 대시 길이

# 간격이 이보다 촘촘하면 QGIS가 화면 가득 선을 그리느라 멈춘 것처럼 보인다.
# 도면 단위로 0.05는 A1 도면에서 20m당 400줄이다. 실무 패턴은 이보다 성기다.
MIN_SPACING = 0.05


@dataclass(frozen=True)
class Family:
    """평행선 한 가족. 이것 하나가 QGIS 채움 심볼 레이어 하나가 된다."""

    angle: float          # 도(degree). 화면 기준 반시계.
    spacing: float        # 선 사이 수직 거리. 도면 단위.
    dashes: tuple[float, ...] = ()   # 비어 있으면 실선


@dataclass(frozen=True)
class Hatch:
    """HATCH 엔티티 하나의 채움 정보."""

    pattern: str
    solid: bool
    families: tuple[Family, ...] = ()

    def key(self) -> str:
        """같은 모양이면 같은 문자열. 레이어 안에서 분류 렌더러의 분류값으로 쓴다."""
        if self.solid:
            return "SOLID"
        parts = [f"{f.angle:.4g}/{f.spacing:.6g}" for f in self.families]
        return f"{self.pattern}|" + "|".join(parts)


@dataclass
class _Pending:
    """파싱 중인 HATCH 하나. 코드가 흩어져 나오므로 모아 두었다가 마지막에 만든다."""

    handle: str | None = None
    pattern: str | None = None
    solid: bool | None = None
    rotation: float = 0.0
    scale: float = 1.0
    angles: list[float] = field(default_factory=list)
    offsets: list[tuple[float, float]] = field(default_factory=list)
    dashes: list[list[float]] = field(default_factory=list)

    def _offset_x(self, value: float) -> None:
        self.offsets.append((value, 0.0))

    def _offset_y(self, value: float) -> None:
        if self.offsets:
            x, _ = self.offsets[-1]
            self.offsets[-1] = (x, value)

    def build(self) -> tuple[str, Hatch] | None:
        if not self.handle or self.solid is None:
            return None
        name = self.pattern or "SOLID"
        if self.solid:
            return self.handle, Hatch(pattern=name, solid=True)

        families: list[Family] = []
        for index, angle in enumerate(self.angles):
            dx, dy = self.offsets[index] if index < len(self.offsets) else (0.0, 0.0)
            # 오프셋은 가족의 좌표계로 주어진다. 선 사이 수직 거리는 y성분이지만,
            # 도면에 따라 x에만 들어오는 경우가 있어 길이로 받는다.
            spacing = abs(dy) or math.hypot(dx, dy)
            spacing *= abs(self.scale) or 1.0
            if spacing < MIN_SPACING:
                continue
            pattern = self.dashes[index] if index < len(self.dashes) else []
            family = Family(
                angle=(angle + self.rotation) % 180.0,
                spacing=spacing,
                dashes=tuple(abs(v) * (abs(self.scale) or 1.0) for v in pattern if v),
            )
            # 가족끼리 기준점만 다르고 각도·간격이 같은 패턴이 흔하다(HARTHOLZ는 3개가
            # 같다). 기준점을 쓰지 않으므로 같은 선을 여러 번 그리는 낭비가 된다.
            if family not in families:
                families.append(family)

        if not families:
            # 정의를 못 읽었으면 단색으로 두는 편이 낫다. 임의로 지어내지 않는다.
            return self.handle, Hatch(pattern=name, solid=True)
        return self.handle, Hatch(pattern=name, solid=False, families=tuple(families))


def read(dxf: Path) -> dict[str, Hatch]:
    """엔티티 핸들 → 채움 정보.

    핸들로 돌려주는 것은 OGR이 `EntityHandle` 필드를 주기 때문이다. 레이어명으로
    묶으면 같은 레이어에 다른 패턴이 섞인 도면에서 어긋난다.

    파일을 끝까지 읽는다. BLOCKS의 첫 ENDSEC에서 멈추면 정작 ENTITIES의 해치를
    통째로 놓쳐 핸들이 하나도 맞지 않는다 (2026-08-18 실측).
    """
    result: dict[str, Hatch] = {}
    current: _Pending | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            built = current.build()
            if built:
                result[built[0]] = built[1]
        current = None

    # 실수 코드는 값이 깨져 있어도 파싱 전체를 멈추지 않는다. 한 엔티티만 포기한다.
    def number(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    with Path(dxf).open("r", encoding="utf-8", errors="replace") as handle:
        code: str | None = None
        for raw in handle:
            value = raw.strip()
            if code is None:
                code = value
                continue
            group, code = code, None

            if group == "0":
                flush()
                if value == "HATCH":
                    current = _Pending()
                continue

            if current is None:
                continue

            if group == "5" and current.handle is None:
                current.handle = value
            elif group == "2" and current.pattern is None:
                current.pattern = value
            elif group == "70" and current.solid is None:
                current.solid = value.strip() == "1"
            elif group == "52":
                current.rotation = number(value) or 0.0
            elif group == "41":
                current.scale = number(value) or 1.0
            elif group == "53":
                angle = number(value)
                if angle is not None:
                    current.angles.append(angle)
                    current.dashes.append([])
            elif group == "45":
                offset = number(value)
                if offset is not None:
                    current._offset_x(offset)
            elif group == "46":
                offset = number(value)
                if offset is not None:
                    current._offset_y(offset)
            elif group == "49":
                dash = number(value)
                if dash is not None and current.dashes:
                    current.dashes[-1].append(dash)

    flush()
    return result
