"""네이버 클라우드 플랫폼 Static Map — 실제 병원 위치 지도 이미지(data URI).

리포트 CSP가 `img-src data:` 라서 외부 이미지 URL은 넣을 수 없다. 생성 시점에
정적 지도 PNG를 받아 **base64 data URI**로 임베드해야 브라우저가 표시한다.
(개방망 + NCP Maps 키 필요. 이 Claude 샌드박스는 지도 호스트가 차단돼 있어,
 베놈 자체 서버/PC 등 개방망 환경에서 생성해야 실제 지도가 채워진다.)

키 발급: 네이버 클라우드 플랫폼(console.ncloud.com) → Services > AI·NAVER API
        > Maps > 이용 신청 → 인증정보(Client ID/Secret)
  - NAVER_MAP_KEY_ID : X-NCP-APIGW-API-KEY-ID
  - NAVER_MAP_KEY    : X-NCP-APIGW-API-KEY

키가 없거나 호출 실패 시 None을 반환하고, 호출자는 개략도+지도 링크로 폴백한다.
"""

import base64
import os
import urllib.parse

from .. import httpx

STATIC_URL = "https://maps.apigw.ntruss.com/map-static/v2/raster"
# 구(舊) 엔드포인트 폴백 (일부 계정은 naveropenapi 도메인만 열려 있음)
STATIC_URL_LEGACY = "https://naveropenapi.apigw.ntruss.com/map-static/v2/raster"


def _keys() -> tuple[str, str]:
    return (os.environ.get("NAVER_MAP_KEY_ID", ""),
            os.environ.get("NAVER_MAP_KEY", ""))


def available() -> bool:
    return all(_keys())


def _fetch(url: str, params: list, kid: str, ksec: str) -> bytes | None:
    q = urllib.parse.urlencode(params, safe=":|,", quote_via=urllib.parse.quote)
    try:
        raw = httpx.get_bytes(url + "?" + q, headers={
            "X-NCP-APIGW-API-KEY-ID": kid,
            "X-NCP-APIGW-API-KEY": ksec,
        }, timeout=30)
    except Exception:
        return None
    # PNG 매직넘버 확인 — 에러 JSON을 이미지로 오인하지 않도록
    if raw and raw[:8] == b"\x89PNG\r\n\x1a\n":
        return raw
    return None


def location_png(lat: float | None, lng: float | None,
                 w: int = 680, h: int = 320, level: int = 16,
                 scale: int = 2) -> bytes | None:
    """병원 좌표(WGS84) 중심의 정적 지도 PNG(마커 포함). 실패 시 None.

    level: 확대(1~20), 16≈동네·블록 수준. scale=2 = 레티나 2배 해상도.
    """
    if not available() or lat is None or lng is None:
        return None
    kid, ksec = _keys()
    params = [
        ("w", w), ("h", h), ("center", f"{lng},{lat}"),
        ("level", level), ("scale", scale), ("format", "png"),
        ("markers", f"type:d|size:mid|color:0x2a78d6|pos:{lng} {lat}"),
    ]
    return (_fetch(STATIC_URL, params, kid, ksec)
            or _fetch(STATIC_URL_LEGACY, params, kid, ksec))


def location_data_uri(lat: float | None, lng: float | None, **kw) -> str | None:
    """정적 지도를 리포트에 바로 넣을 수 있는 data:image/png;base64 URI로."""
    raw = location_png(lat, lng, **kw)
    if not raw:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
