"""네이버 통합검색 콘텐츠 영역 커넥터 — 공식 오픈 API로 측정 가능한 전 영역.

통합검색 화면은 키워드마다 다른 영역 조합(파워링크, 플레이스, 블로그/카페
스마트블록, 브랜드 콘텐츠, 뉴스, 이미지 등)으로 구성된다. 이 커넥터는
그중 **공식 오픈 API가 제공되는 영역**을 모두 조회해, 키워드별로
① 해당 영역에 콘텐츠가 존재하는지(present)
② 상위 N건 안에 병원명이 포함된 콘텐츠가 있는지(exposed, position)
를 판정한다. (비로그인 오픈 API, 지역검색과 동일 키)

측정 영역 (공식 API):
- 블로그  /v1/search/blog.json        — 통합영역(스마트블록)·VIEW의 주요 소스
- 카페    /v1/search/cafearticle.json — 통합영역·카페 블록 소스
- 웹문서  /v1/search/webkr.json       — 웹사이트 영역
- 뉴스    /v1/search/news.json        — 뉴스 영역
- 이미지  /v1/search/image            — 이미지 영역
- 지식iN  /v1/search/kin.json         — 지식iN 영역

측정 제외 (공식 API 미제공 — 법무 검토상 화면 수집 보류):
- 파워링크/브랜드검색(광고): 검색광고 API는 자사 광고 성과만 제공.
  경쟁 광고 관찰은 SERP 수집이 필요하며 법무 승인 전 금지 (검토 메모 B-7)
- 스마트블록의 실제 화면 배치·순서: 블로그/카페 API 노출을 근사 지표로 사용

주의:
- API 결과는 실제 통합검색 화면 구성·순서와 다를 수 있다 (고지 필수)
- 제목/요약 스니펫만 판정에 사용, 본문 미수집·미저장
- 판정 결과(존재/노출/위치)만 고객 자료에 사용
"""

import json
import re
import urllib.parse

from .. import config, httpx

# key: (표시명, 엔드포인트, 통합검색 화면에서의 대응 영역 설명)
SECTIONS = {
    "blog": ("블로그", "https://openapi.naver.com/v1/search/blog.json",
             "통합영역·스마트블록의 블로그 콘텐츠"),
    "cafe": ("카페", "https://openapi.naver.com/v1/search/cafearticle.json",
             "통합영역·카페 블록"),
    "web": ("웹문서", "https://openapi.naver.com/v1/search/webkr.json",
            "웹사이트 영역"),
    "news": ("뉴스", "https://openapi.naver.com/v1/search/news.json",
             "뉴스 영역"),
    "image": ("이미지", "https://openapi.naver.com/v1/search/image",
              "이미지 영역"),
    "kin": ("지식iN", "https://openapi.naver.com/v1/search/kin.json",
            "지식iN 영역"),
}

DEFAULT_DISPLAY = 30  # "API 결과 상위 30건 내" 기준

_TAG_RE = re.compile(r"<[^>]+>")


def _norm(s: str) -> str:
    """병원명 매칭용 정규화 — 공백 제거 + 의원/병원 접미사 제거."""
    s = "".join((s or "").split())
    for suffix in ("의원", "병원", "한의원", "치과의원", "클리닉"):
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
            break
    return s


def _search(url: str, keyword: str, display: int) -> list[dict]:
    q = urllib.parse.urlencode({"query": keyword, "display": min(display, 100)})
    payload = json.loads(httpx.get_bytes(
        url + "?" + q,
        headers={
            "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
        },
        timeout=30,
    ))
    return payload.get("items", [])


def content_exposure(keyword: str, hospital_name: str,
                     display: int = DEFAULT_DISPLAY) -> dict:
    """키워드에 대한 영역별 {present, exposed, position, label}.

    - present=False  → 이 키워드에는 해당 영역 콘텐츠가 사실상 없음 (영역 비활성)
    - exposed=None   → 조회 실패 (미검증 — 미노출로 단정하지 않음)
    - position       → API 결과 내 1-기준 순번 (실제 화면 순위 아님)
    """
    if not config.naver_available():
        return {k: {"present": False, "exposed": None, "position": None, "label": lbl}
                for k, (lbl, _, _) in SECTIONS.items()}

    norm_h = _norm(hospital_name)
    out = {}
    for key, (label, url, _desc) in SECTIONS.items():
        try:
            items = _search(url, keyword, display)
        except Exception:
            out[key] = {"present": None, "exposed": None, "position": None, "label": label}
            continue
        present = len(items) > 0
        exposed, position = False, None
        for i, item in enumerate(items):
            text = _TAG_RE.sub("", (item.get("title") or "") + " "
                               + (item.get("description") or ""))
            if norm_h and norm_h in "".join(text.split()):
                exposed, position = True, i + 1
                break
        out[key] = {"present": present, "exposed": exposed if present else False,
                    "position": position, "label": label}
    return out


def coverage_summary(results: list[dict]) -> dict:
    """키워드별 content_exposure 결과 → 영역별 (활성 키워드 수, 노출 키워드 수)."""
    summary = {k: {"present": 0, "exposed": 0, "label": SECTIONS[k][0]}
               for k in SECTIONS}
    for r in results:
        for k in SECTIONS:
            cell = r.get(k) or {}
            if cell.get("present"):
                summary[k]["present"] += 1
                if cell.get("exposed"):
                    summary[k]["exposed"] += 1
    return {"total_keywords": len(results), "sections": summary}
