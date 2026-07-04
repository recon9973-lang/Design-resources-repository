"""네이버 검색광고 키워드도구 API 커넥터 — 절대 월 검색량 + 연관 검색어.

일반 오픈 API(비로그인)로는 '검색량'과 '연관검색어'를 얻을 수 없다. 이 둘은
네이버 검색광고(광고주) 계정의 키워드도구 API에서만 공식 제공된다.
  https://naver.github.io/searchad-apidoc/  (키워드도구: GET /keywordstool)

인증: 광고주 계정에서 발급하는 3종 자격증명 + HMAC-SHA256 서명
  - X-API-KEY  : 액세스 라이선스 (API_KEY)
  - X-Customer : 고객(광고주) ID (CUSTOMER_ID)
  - X-Signature: base64( HMAC-SHA256(SECRET_KEY, "{timestamp}.{method}.{uri}") )
  - X-Timestamp: 밀리초 단위 현재시각

키가 없거나 호출 실패 시 None/빈 값을 반환하고, 호출자는 '미연동'으로 처리한다.
(DataLab은 상대 추세지수만 주므로 절대 검색량 표기에는 쓰지 않는다.)
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse

from .. import httpx

BASE_URL = "https://api.searchad.naver.com"
KEYWORDSTOOL_URI = "/keywordstool"


def _creds() -> tuple[str, str, str]:
    return (os.environ.get("NAVER_AD_API_KEY", ""),
            os.environ.get("NAVER_AD_SECRET_KEY", ""),
            os.environ.get("NAVER_AD_CUSTOMER_ID", ""))


def available() -> bool:
    return all(_creds())


def _sign(secret: str, timestamp: str, method: str, uri: str) -> str:
    """검색광고 API 서명: base64(HMAC-SHA256(secret, "ts.METHOD.uri"))."""
    msg = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _headers(method: str, uri: str) -> dict:
    api_key, secret, customer = _creds()
    ts = str(int(time.time() * 1000))
    return {
        "X-Timestamp": ts,
        "X-API-KEY": api_key,
        "X-Customer": str(customer),
        "X-Signature": _sign(secret, ts, method, uri),
    }


def _to_int(v) -> int | None:
    """monthlyPcQcCnt 등은 정수 또는 '< 10' 문자열로 온다. 정수로 정규화."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if "<" in s:          # '< 10' → 저조 검색량, 보수적으로 5로 추정
        return 5
    try:
        return int(float(s))
    except ValueError:
        return None


def keyword_report(hint_keywords: list[str], limit: int = 60) -> list[dict] | None:
    """힌트 키워드로 키워드도구 조회. 힌트 자신 + 연관 키워드를 함께 반환.

    hint_keywords: 최대 5개 (공백 제거 후 콤마 결합). 병원 진료·지역 키워드 권장.
    반환: [{kw, pc, mobile, total, comp}]  (검색량 총합 내림차순), 실패 시 None.
      pc/mobile/total = 월간 검색수, comp = 경쟁정도(낮음/중간/높음).
    네이버 규격상 hintKeywords의 공백은 제거해야 한다.
    """
    if not available() or not hint_keywords:
        return None
    hints = ",".join(k.replace(" ", "") for k in hint_keywords[:5] if k)
    q = urllib.parse.urlencode({"hintKeywords": hints, "showDetail": "1"})
    url = f"{BASE_URL}{KEYWORDSTOOL_URI}?{q}"
    try:
        raw = httpx.get_bytes(url, headers=_headers("GET", KEYWORDSTOOL_URI),
                              timeout=30)
        data = json.loads(raw)
    except Exception:
        return None
    out = []
    for it in (data.get("keywordList") or []):
        pc = _to_int(it.get("monthlyPcQcCnt"))
        mo = _to_int(it.get("monthlyMobileQcCnt"))
        total = (pc or 0) + (mo or 0)
        out.append({
            "kw": it.get("relKeyword", ""),
            "pc": pc, "mobile": mo, "total": total,
            "comp": it.get("compIdx"),   # '낮음'/'중간'/'높음'
        })
    out.sort(key=lambda r: r["total"], reverse=True)
    return out[:limit]


def volumes_for(keywords: list[str]) -> dict[str, dict] | None:
    """진단 키워드 각각의 월 검색량을 {정규화kw: {pc,mobile,total,comp}}로.

    키워드도구는 힌트당 대량의 연관어를 함께 주므로, 진단 키워드 원문과
    (공백 제거) 정규화 매칭해 해당 항목만 추린다. 매칭 실패 키는 제외.
    """
    if not available() or not keywords:
        return None
    norm = lambda s: "".join((s or "").split())
    wanted = {norm(k): k for k in keywords}
    found: dict[str, dict] = {}
    # 힌트는 5개씩 끊어 여러 번 호출 (연관어로 서로의 값도 채워질 수 있음)
    for i in range(0, len(keywords), 5):
        rep = keyword_report(keywords[i:i + 5])
        if not rep:
            continue
        for row in rep:
            nk = norm(row["kw"])
            if nk in wanted and nk not in found:
                found[nk] = row
    return found or None
