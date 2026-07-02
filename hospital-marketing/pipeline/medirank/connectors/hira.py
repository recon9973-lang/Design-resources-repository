"""건강보험심사평가원 병원정보서비스 커넥터.

공공데이터포털: https://www.data.go.kr (활용신청 후 serviceKey 발급, 무료)
엔드포인트: getHospBasisList — 병원명/지역/진료과 기준 병원 기본정보 조회.

DATA_GO_KR_SERVICE_KEY가 없으면 fixtures/hira_sample.json 목업으로 동작한다.
출처·갱신일 표기를 위해 모든 레코드에 source/source_updated_at을 붙인다.
"""

import json
import urllib.parse
import xml.etree.ElementTree as ET

from .. import config, httpx

API_BASE = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"


def _normalize(name: str) -> str:
    return "".join(name.split()).replace("의원", "").replace("병원", "")


def _record(name, address, lat, lng, dept_code, dept_name, source_id, updated):
    return {
        "name": name,
        "normalized_name": _normalize(name),
        "address": address,
        "latitude": lat,
        "longitude": lng,
        "department_code": dept_code,
        "department_name": dept_name,
        "source": "hira",
        "source_id": source_id,
        "source_updated_at": updated,
        "status": "operating",
    }


def fetch_hospitals(sido_cd: str = "110000", dgsbjt_cd: str | None = None,
                    rows: int = 500) -> list[dict]:
    """지역(시도코드)·진료과목코드 기준 병원 목록. 키가 없으면 목업 반환."""
    if not config.hira_available():
        return load_fixture()

    params = {
        "serviceKey": config.DATA_GO_KR_SERVICE_KEY,
        "sidoCd": sido_cd,
        "numOfRows": rows,
        "pageNo": 1,
    }
    if dgsbjt_cd:
        params["dgsbjtCd"] = dgsbjt_cd
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    root = ET.fromstring(httpx.get_bytes(url, timeout=90))

    out = []
    for item in root.iter("item"):
        get = lambda tag: (item.findtext(tag) or "").strip()
        try:
            lat, lng = float(get("YPos")), float(get("XPos"))
        except ValueError:
            lat = lng = None
        out.append(_record(
            name=get("yadmNm"), address=get("addr"), lat=lat, lng=lng,
            dept_code=get("dgsbjtCd"), dept_name=get("clCdNm"),
            source_id=get("ykiho"), updated=get("estbDd") or None,
        ))
    return out


def fetch_hospitals_radius(x_pos: float, y_pos: float, radius_m: int,
                           dgsbjt_cd: str | None = None, rows: int = 300) -> list[dict]:
    """좌표+반경 기준 병원 목록 (getHospBasisList의 xPos/yPos/radius 파라미터).

    반경 계산을 서버가 해주므로 0단계 검증에서 하버사인 결과와 교차 확인한다.
    키가 없으면 목업 반환.
    """
    if not config.hira_available():
        return load_fixture()

    params = {
        "serviceKey": config.DATA_GO_KR_SERVICE_KEY,
        "xPos": x_pos, "yPos": y_pos, "radius": radius_m,
        "numOfRows": rows, "pageNo": 1,
    }
    if dgsbjt_cd:
        params["dgsbjtCd"] = dgsbjt_cd
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    root = ET.fromstring(httpx.get_bytes(url, timeout=90))

    out = []
    for item in root.iter("item"):
        get = lambda tag: (item.findtext(tag) or "").strip()
        try:
            lat, lng = float(get("YPos")), float(get("XPos"))
        except ValueError:
            lat = lng = None
        out.append(_record(
            name=get("yadmNm"), address=get("addr"), lat=lat, lng=lng,
            dept_code=params.get("dgsbjtCd", ""), dept_name=get("clCdNm"),
            source_id=get("ykiho"), updated=get("estbDd") or None,
        ))
    return out


def load_fixture() -> list[dict]:
    path = config.FIXTURES_DIR / "hira_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))
