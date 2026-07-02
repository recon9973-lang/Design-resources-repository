"""좌표/거리 유틸. PostGIS 도입 전 검증용 하버사인 구현."""

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리(m)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def within_radius(base: dict, candidates: list[dict], radius_m: int) -> list[dict]:
    """반경 내 병원 목록. 각 항목에 distance_m을 붙여 거리 오름차순으로 반환.

    base/candidates는 latitude/longitude 키를 가진 dict.
    """
    out = []
    for c in candidates:
        if c.get("latitude") is None or c.get("longitude") is None:
            continue
        d = haversine_m(base["latitude"], base["longitude"], c["latitude"], c["longitude"])
        if 0 < d <= radius_m:
            out.append({**c, "distance_m": round(d, 1)})
    out.sort(key=lambda x: x["distance_m"])
    return out
