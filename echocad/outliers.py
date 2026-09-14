# 좌표가 깨진 피처를 골라내는 판정 규칙. 데이터를 지우는 결정이라 QGIS 없이 검증되게 분리했다
from __future__ import annotations

# 도면 본체 폭의 이 배수를 넘으면 버린다. 실측 근거는 research R14 참조.
DEFAULT_FACTOR = 100.0
# 표본이 적으면 사분위 통계가 의미 없으므로 아무것도 버리지 않는다.
MIN_SAMPLE = 20


def outlier_indices(points, factor: float = DEFAULT_FACTOR) -> set[int]:
    """중앙값에서 터무니없이 떨어진 점의 인덱스.

    평균·최댓값을 쓰면 이상치 자신에게 끌려가므로 중앙값과 사분위 폭을 쓴다.
    GDAL의 DXF 해치 파서가 이따금 10^13 수준의 좌표를 뱉는데, 한 개만 있어도
    전체 범위가 부풀어 도면이 빈 화면처럼 보인다.
    """
    if len(points) < MIN_SAMPLE:
        return set()

    xs = sorted(p[0] for p in points)
    ys = sorted(p[1] for p in points)
    low, high = len(xs) // 4, 3 * len(xs) // 4
    span = max(xs[high] - xs[low], ys[high] - ys[low], 1.0)
    limit = span * factor
    mid_x, mid_y = xs[len(xs) // 2], ys[len(ys) // 2]

    return {
        index for index, (x, y) in enumerate(points)
        if abs(x - mid_x) > limit or abs(y - mid_y) > limit
    }


# 첫 화면에서 뺄지 정하는 배수. 지우는 기준(DEFAULT_FACTOR)보다 훨씬 엄격하다 —
# 지우는 것은 되돌릴 수 없지만 보기는 사용자가 언제든 넓힐 수 있기 때문이다.
VIEW_FACTOR = 3.0
# 이 비율을 넘게 빠지면 먼 쪽도 도면의 일부로 본다. 도면 두 장이 나란히 놓인 배치를
# 한쪽만 보여 주면 안 된다.
VIEW_MAX_TRIM = 0.01


def view_bounds(points, factor: float = VIEW_FACTOR, max_trim: float = VIEW_MAX_TRIM):
    """가져온 직후 화면에 잡아 줄 범위. (xmin, ymin, xmax, ymax) 또는 None.

    **데이터는 건드리지 않는다.** 보기만 좁힌다.

    이게 필요한 이유 — 도면에는 본체에서 멀리 떨어진 정당한 엔티티가 종종 있다.
    2026-08-23 실측 `mech-iso.dwg` 는 도면이 `3247,1230 : 3667,1527` 에 있는데
    레이어 `0` 의 `AcDbPoint` 하나가 원점(0,0)에 있다. 전체 범위로 화면을 잡으면
    도면이 구석에 콩알만 하게 보인다. 그 점은 진짜 데이터라 지우면 안 된다.
    """
    if not points:
        return None

    far = outlier_indices(points, factor)
    # 많이 빠지면 그쪽도 도면이다. 하나뿐인 유령 점만 빼는 것이 목적이다.
    if far and len(far) <= max(1, int(len(points) * max_trim)):
        kept = [point for index, point in enumerate(points) if index not in far]
    else:
        kept = points

    xs = [x for x, _ in kept]
    ys = [y for _, y in kept]
    return (min(xs), min(ys), max(xs), max(ys))
