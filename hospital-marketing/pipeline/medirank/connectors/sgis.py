"""SGIS 통계지리정보서비스 커넥터 (인구 지표 — 잠재 수요 참고지표).

https://sgis.kostat.go.kr/developer/
- consumer_key/secret으로 액세스 토큰 발급 후 통계 API 호출
- 2026년 현재 sgisapi.kostat.go.kr은 sgisapi.mods.go.kr로 리다이렉트된다

키가 없거나 호출 실패 시 None을 반환하고, 호출자는 기본값으로 대체한다.
"""

import json
import os
import urllib.parse

from .. import httpx
from .. import config  # noqa: F401  (.env 로딩 side effect)

AUTH_URL = "https://sgisapi.kostat.go.kr/OpenAPI3/auth/authentication.json"
POP_URL = "https://sgisapi.kostat.go.kr/OpenAPI3/stats/population.json"


def _keys() -> tuple[str, str]:
    return (os.environ.get("SGIS_CONSUMER_KEY", ""),
            os.environ.get("SGIS_CONSUMER_SECRET", ""))


def available() -> bool:
    return all(_keys())


def _get_json(url: str) -> dict:
    """302 리다이렉트를 따라가며 JSON 응답을 읽는다."""
    return json.loads(httpx.get_bytes(url, timeout=60))


def get_access_token() -> str | None:
    if not available():
        return None
    key, secret = _keys()
    q = urllib.parse.urlencode({"consumer_key": key, "consumer_secret": secret})
    data = _get_json(AUTH_URL + "?" + q)
    if data.get("errCd") != 0:
        return None
    return data["result"]["accessToken"]


def area_stats(adm_cd: str, year: str = "2023") -> dict | None:
    """행정구역 코드(adm_cd)의 인구/상권 지표. 실패 시 None.

    adm_cd 예: 서울 '11', 강남구 '11230' (SGIS 코드 체계)
    반환: tot_ppltn(총인구), ppltn_dnsty(명/km²), avg_age,
          employee_cnt(종사자), corp_cnt(사업체), adm_nm
    """
    token = get_access_token()
    if not token:
        return None
    q = urllib.parse.urlencode({
        "accessToken": token, "year": year, "adm_cd": adm_cd, "low_search": "0",
    })
    try:
        data = _get_json(POP_URL + "?" + q)
        if data.get("errCd") != 0 or not data.get("result"):
            return None
        r = data["result"][0]
        num = lambda k: float(r[k]) if r.get(k) not in (None, "N/A") else None
        return {
            "adm_nm": r.get("adm_nm"),
            "tot_ppltn": num("tot_ppltn"),
            "ppltn_dnsty": num("ppltn_dnsty"),
            "avg_age": num("avg_age"),
            "employee_cnt": num("employee_cnt"),
            "corp_cnt": num("corp_cnt"),
            "year": year,
        }
    except Exception:
        return None


STAGE_URL = "https://sgisapi.kostat.go.kr/OpenAPI3/addr/stage.json"


def find_adm_cd(city: str, gu: str | None = None) -> str | None:
    """지역 이름으로 SGIS 행정구역 코드를 찾는다.

    city 예: '대구', '서울' / gu 예: '수성구'. gu가 없으면 시도 코드 반환.
    """
    token = get_access_token()
    if not token or not city:
        return None
    try:
        data = _get_json(STAGE_URL + "?" + urllib.parse.urlencode({"accessToken": token}))
        sido = next((r for r in data.get("result", [])
                     if city in (r.get("addr_name") or "")), None)
        if not sido:
            return None
        if not gu:
            return sido.get("cd")
        data = _get_json(STAGE_URL + "?" + urllib.parse.urlencode(
            {"accessToken": token, "cd": sido["cd"]}))
        sgg = next((r for r in data.get("result", [])
                    if gu in (r.get("addr_name") or "")), None)
        return sgg.get("cd") if sgg else sido.get("cd")
    except Exception:
        return None


def population_of(adm_cd: str, year: str = "2023") -> int | None:
    """행정구역 총인구 (하위 호환용)."""
    stats = area_stats(adm_cd, year)
    if not stats or stats.get("tot_ppltn") is None:
        return None
    return int(stats["tot_ppltn"])
