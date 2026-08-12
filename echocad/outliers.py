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
