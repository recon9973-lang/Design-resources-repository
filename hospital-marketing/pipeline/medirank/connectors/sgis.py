"""SGIS 통계지리정보서비스 커넥터 (인구 지표 — 잠재 수요 참고지표).

https://sgis.kostat.go.kr/developer/
- consumer_key/secret으로 액세스 토큰 발급 후 통계 API 호출
- 2026년 현재 sgisapi.kostat.go.kr은 sgisapi.mods.go.kr로 리다이렉트된다

키가 없거나 호출 실패 시 None을 반환하고, 호출자는 기본값으로 대체한다.
"""

import json
import urllib.parse

from .. import httpx
from .. import config

AUTH_URL = "https://sgisapi.kostat.go.kr/OpenAPI3/auth/authentication.json"
POP_URL = "https://sgisapi.kostat.go.kr/OpenAPI3/stats/population.json"


def _keys() -> tuple[str, str]:
    return config.sgis_creds()


def available() -> bool:
    return config.sgis_available()


def _get_json(url: str) -> dict:
    """302 리다이렉트를 따라가며 JSON 응답을 읽는다."""
    return json.loads(httpx.get_bytes(url, timeout=60))


# 토큰 캐시 — SGIS 액세스 토큰은 발급 후 일정 시간 유효하다. 캐시하지 않으면
# 진단 1건이 find_adm_cd·area_stats·pop_weighted_density마다 토큰을 새로 발급해
# 인증(auth) 호출이 폭증 → SGIS가 인증을 제한(-401)한다. 프로세스 내 재사용으로 방지.
_TOKEN = {"value": None, "last_error": None}


def get_access_token(force: bool = False) -> str | None:
    if not available():
        return None
    if _TOKEN["value"] and not force:
        return _TOKEN["value"]
    key, secret = _keys()
    q = urllib.parse.urlencode({"consumer_key": key, "consumer_secret": secret})
    try:
        data = _get_json(AUTH_URL + "?" + q)
    except Exception as e:
        _TOKEN["last_error"] = {"errCd": "EXC", "errMsg": str(e)}
        return None
    if data.get("errCd") != 0:
        _TOKEN["last_error"] = {"errCd": data.get("errCd"), "errMsg": data.get("errMsg")}
        _TOKEN["value"] = None
        return None
    _TOKEN["value"] = data["result"]["accessToken"]
    _TOKEN["last_error"] = None
    return _TOKEN["value"]


def auth_status() -> dict:
    """SGIS 인증 상태 진단용 — 마지막 인증 에러/토큰 보유 여부."""
    tok = get_access_token()
    return {"ok": bool(tok), "last_error": _TOKEN["last_error"]}


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


def population_composition(adm_cd: str, year: str = "2023") -> dict | None:
    """행정구역 인구 구성 — 총인구·평균연령·남녀·연령대. SGIS 실측만.

    조작 금지: SGIS가 주지 않는 항목은 None(미측정)으로 둔다. 남녀는 gender 필터,
    연령대는 age_type 코드 순회로 조회한다. (SGIS OpenAPI3 population.json)
    가장 적은 호출로 얻도록 total→남/여 순서로 조회(쿼터 절약, 토큰 캐시 사용).
    """
    token = get_access_token()
    if not token:
        return None

    def _pop(extra):
        q = urllib.parse.urlencode({"accessToken": token, "year": year,
                                    "adm_cd": adm_cd, "low_search": "0", **extra})
        try:
            d = _get_json(POP_URL + "?" + q)
            if d.get("errCd") != 0 or not d.get("result"):
                return None
            return d["result"]
        except Exception:
            return None

    numf = lambda r, k: (float(r[k]) if r and r.get(k) not in (None, "N/A") else None)
    base = _pop({})
    if not base:
        return None
    b = base[0]
    out = {"year": year, "tot_ppltn": numf(b, "tot_ppltn"), "avg_age": numf(b, "avg_age"),
           "male_ppltn": None, "female_ppltn": None, "male_pct": None, "female_pct": None,
           "age_bands": None}

    # 성별: gender 필터(1=남, 2=여). 파라미터가 무시되면 total과 동일해지므로 검증 필요.
    male = _pop({"gender": "1"})
    female = _pop({"gender": "2"})
    m = numf(male[0], "tot_ppltn") if male else None
    f = numf(female[0], "tot_ppltn") if female else None
    # 남/여가 total과 같으면(=필터 무시) 신뢰 불가 → 미측정 유지(조작 금지)
    if m is not None and f is not None and m != out["tot_ppltn"] and f != out["tot_ppltn"] and (m + f) > 0:
        out["male_ppltn"], out["female_ppltn"] = int(m), int(f)
        out["male_pct"] = round(100 * m / (m + f), 1)
        out["female_pct"] = round(100 * f / (m + f), 1)

    # 연령대: age_type 코드별 인구. SGIS 응답 필드는 개방망 실검증 후 확정.
    bands = []
    AGE_LABELS = [("1", "0-9세"), ("2", "10대"), ("3", "20대"), ("4", "30대"),
                  ("5", "40대"), ("6", "50대"), ("7", "60대"), ("8", "70대 이상")]
    for code, label in AGE_LABELS:
        rows = _pop({"age_type": code})
        v = numf(rows[0], "ppltn") if rows else (numf(rows[0], "tot_ppltn") if rows else None)
        if v is not None and v != out["tot_ppltn"]:
            bands.append({"label": label, "ppltn": int(v)})
    if bands:
        total_b = sum(x["ppltn"] for x in bands) or 1
        for x in bands:
            x["pct"] = round(100 * x["ppltn"] / total_b, 1)
        out["age_bands"] = bands
    return out


STAGE_URL = "https://sgisapi.kostat.go.kr/OpenAPI3/addr/stage.json"


# 도(道) 축약형 → SGIS 행정구역 표기(addr_name '경상북도' 등에 부분일치하도록).
# 키워드용 축약('경북')은 SGIS addr_name의 부분문자열이 아니므로 확장해야 매칭된다.
_SIDO_ALIAS = {"경북": "경상북", "경남": "경상남", "전북": "전라북",
               "전남": "전라남", "충북": "충청북", "충남": "충청남"}


def find_adm_cd(city: str, gu: str | None = None) -> str | None:
    """지역 이름으로 SGIS 행정구역 코드를 찾는다.

    city 예: '대구', '서울', '경북'(→'경상북'으로 확장) / gu 예: '수성구'.
    gu가 없으면 시도 코드 반환.
    """
    token = get_access_token()
    if not token or not city:
        return None
    city = _SIDO_ALIAS.get(city, city)
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


def pop_weighted_density(adm_cd: str, year: str = "2023") -> float | None:
    """행정동 분해(low_search=1)로 계산한 인구가중 밀도(체감 밀도, 명/km²).

    넓은 시(춘천 등)는 외곽 읍·면이 '시 평균 밀도'를 크게 끌어내려, 도심 업체의
    실제 생활권 밀도를 과소평가한다. 인구가중 밀도 = Σ(인구×밀도)/Σ(인구) 는
    '사람이 실제로 몰려 사는 곳의 밀도'라, 도심 상권 기준으로 훨씬 현실적이다.
    서울 강남처럼 전역이 고밀도인 지역은 평균과 거의 같아 부작용이 없다.
    """
    token = get_access_token()
    if not token or not adm_cd:
        return None
    q = urllib.parse.urlencode({
        "accessToken": token, "year": year, "adm_cd": adm_cd, "low_search": "1",
    })
    try:
        res = _get_json(POP_URL + "?" + q).get("result", [])
        num = den = 0.0
        for r in res:
            p, d = r.get("tot_ppltn"), r.get("ppltn_dnsty")
            if p in (None, "N/A") or d in (None, "N/A"):
                continue
            p, d = float(p), float(d)
            if d <= 0 or p <= 0:
                continue
            num += p * d
            den += p
        return round(num / den, 1) if den else None
    except Exception:
        return None


def population_of(adm_cd: str, year: str = "2023") -> int | None:
    """행정구역 총인구 (하위 호환용)."""
    stats = area_stats(adm_cd, year)
    if not stats or stats.get("tot_ppltn") is None:
        return None
    return int(stats["tot_ppltn"])
