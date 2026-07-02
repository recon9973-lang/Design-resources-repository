"""네이버 지역 검색 API 커넥터 (비로그인 오픈 API).

https://developers.naver.com/docs/serviceapi/search/local/local.md
- 애플리케이션 등록 후 Client ID/Secret 발급
- display 최대 5 — 결과에 없으면 "공식 API 기준 미노출"로 기록한다
- 호출 한도(기본 25,000/일)와 표시 정책을 준수한다. 대량 반복 호출 금지.
- 원본 payload는 고객 화면에 노출하지 않고 internal_evidence_items로만 보관한다.

키가 없으면 fixtures/naver_local_sample.json 목업으로 동작한다.
"""

import json
import re
import urllib.parse

from .. import config, httpx

API_URL = "https://openapi.naver.com/v1/search/local.json"
MAX_DISPLAY = 5  # 지역 검색 API 상한

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s or "")


def search_local(keyword: str) -> list[dict]:
    """키워드의 지역 검색 결과(최대 5건). rank는 1부터."""
    if not config.naver_available():
        return _fixture_results(keyword)

    q = urllib.parse.urlencode({"query": keyword, "display": MAX_DISPLAY, "sort": "random"})
    payload = json.loads(httpx.get_bytes(
        API_URL + "?" + q,
        headers={
            "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
        },
        timeout=30,
    ))
    return [
        {
            "rank": i + 1,
            "title": _strip_tags(item.get("title")),
            "address": item.get("roadAddress") or item.get("address"),
            "category": item.get("category"),
            "link": item.get("link"),
        }
        for i, item in enumerate(payload.get("items", []))
    ]


def keyword_exposure(keyword: str, hospital_name: str) -> dict:
    """키워드에 대한 내 병원의 공식 API 기준 노출 여부/위치.

    이름 정규화 일치는 초기 휴리스틱이다. 운영 시에는 주소/좌표 매칭과
    confidence_score 기반 수동 검수(0단계 성공 기준 85%)로 보강한다.
    """
    results = search_local(keyword)
    norm = "".join(hospital_name.split())
    for r in results:
        if norm in "".join((r["title"] or "").split()):
            return {"keyword": keyword, "exposed": True, "rank": r["rank"], "results": results}
    return {"keyword": keyword, "exposed": False, "rank": None, "results": results}


def _fixture_results(keyword: str) -> list[dict]:
    path = config.FIXTURES_DIR / "naver_local_sample.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get(keyword, [])
