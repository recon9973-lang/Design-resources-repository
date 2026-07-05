"""네이버 플레이스 공개 집계값(리뷰수·방문자리뷰·블로그리뷰·사진수) 수집 — 내부·대면용.

법무/신뢰도 원칙 (중요):
- 리뷰 '본문'은 절대 수집하지 않는다. 공개 '집계 수치'(개수/평점)만 대상.
- 이 자료는 **관리자단 내부·대면 자료 전용**(visibility_scope=internal_admin_only)이며,
  법무 검토(약관·수집 방식) 전까지 고객 리포트/PDF에 직접 노출하지 않는다.
- 자동 파싱은 페이지 구조에 의존해 깨질 수 있으므로, **확신이 없으면 값을 만들지 않고
  None(미측정)을 반환**한다. 조작·추정값 금지. 반환값은 verified=False(미검수)로 표시되어
  사람이 실제 플레이스와 대조 확인한 뒤에만 신뢰한다.
- 오픈 API(openapi.naver.com)는 이 지표를 제공하지 않아, 지도 계열 호스트를 쓴다.
  해당 호스트가 차단된 환경(예: 제한망)에서는 자동으로 None을 반환한다.

사용: fetch_place_metrics(place_url 또는 place_name) → dict|None
"""

import json
import re
import urllib.parse
from datetime import datetime, timezone

from .. import httpx

# 지도 검색 allSearch — 장소 요약(집계 수치 포함). 개방망에서만 도달.
ALLSEARCH = "https://map.naver.com/p/api/search/allSearch"

# 집계 수치로 인정할 키 후보 (구조 변경 대비 넓게 탐색). 값은 정수여야 한다.
_COUNT_KEYS = {
    "review": ("reviewCount", "totalReviewCount", "review_count"),
    "visitor_review": ("visitorReviewCount", "visitorReviewsTotal", "visitor_review_count"),
    "blog_review": ("blogCafeReviewCount", "blogReviewCount", "blog_review_count"),
    "photo": ("imageCount", "photoCount", "photo_count"),
}
_RATING_KEYS = ("visitorReviewScore", "reviewScore", "rating", "score")


def _first_int(d: dict, keys) -> int | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and 0 <= v <= 10_000_000:
            return v
        if isinstance(v, str) and v.replace(",", "").isdigit():
            n = int(v.replace(",", ""))
            if 0 <= n <= 10_000_000:
                return n
    return None


def _first_rating(d: dict, keys) -> float | None:
    for k in keys:
        v = d.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 <= f <= 5.0:
            return round(f, 2)
    return None


def _extract(place: dict) -> dict | None:
    """장소 객체 하나에서 집계 수치를 추출. 유효 지표가 하나도 없으면 None."""
    out = {}
    for name, keys in _COUNT_KEYS.items():
        n = _first_int(place, keys)
        if n is not None:
            out[name] = n
    rating = _first_rating(place, _RATING_KEYS)
    if rating is not None:
        out["rating"] = rating
    # 최소 한 개의 실제 지표가 있어야 인정(빈 값·전부 0 방어는 호출자에서 판단)
    return out or None


def fetch_place_metrics(name: str, place_url: str | None = None) -> dict | None:
    """장소명(+선택 플레이스 URL)으로 공개 집계 수치를 수집. 실패/불확실 시 None.

    반환(성공 시):
      {review, visitor_review, blog_review, photo, rating(있으면),
       source, collected_at, visibility_scope, verified(False=미검수)}
    """
    q = (place_url or name or "").strip()
    if not q:
        return None
    url = ALLSEARCH + "?" + urllib.parse.urlencode({"query": q, "type": "all"})
    try:
        raw = httpx.get_bytes(url, headers={"Accept": "application/json"}, timeout=20)
        data = json.loads(raw)
    except Exception:
        return None  # 호스트 차단·타임아웃·비JSON → 미측정(값 만들지 않음)

    # 응답 구조는 버전에 따라 다르다. place 목록을 넓게 탐색하되, 숫자만 신뢰.
    candidates = []
    try:
        res = (data.get("result") or {}).get("place") or {}
        candidates = res.get("list") or []
    except AttributeError:
        candidates = []
    if not isinstance(candidates, list) or not candidates:
        return None

    metrics = _extract(candidates[0]) if isinstance(candidates[0], dict) else None
    if not metrics:
        return None
    metrics.update({
        "source": "naver_map_allsearch",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "visibility_scope": "internal_admin_only",   # 대면·내부 전용 (법무 검토 전)
        "verified": False,                            # 미검수 — 사람이 실측 대조 필요
    })
    return metrics
