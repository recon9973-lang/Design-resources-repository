"""환경설정. API 키는 환경변수 또는 .env 파일로만 주입한다(저장소 커밋 금지)."""

import os
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PIPELINE_DIR / "fixtures"


def _load_dotenv() -> None:
    env_path = PIPELINE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# 공공데이터포털 (건강보험심사평가원 병원정보서비스)
DATA_GO_KR_SERVICE_KEY = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")

# 네이버 개발자센터 오픈 API (비로그인 방식)
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# 검색광고(NAVER_AD_*)·NCP Maps(NAVER_MAP_*)·SGIS 자격증명은 아래 접근자 함수로만
# 노출한다(호출 시점 조회 → .env 갱신 반영, 토큰 정의 일원화). 상수 미정의.

VALID_RADII_M = (500, 1000, 1500, 2000)


def hira_available() -> bool:
    return bool(DATA_GO_KR_SERVICE_KEY)


def naver_available() -> bool:
    return bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)


# --- 자격증명 단일 접근 지점 -------------------------------------------------
# 커넥터는 os.environ을 직접 읽지 말고 아래 접근자만 쓴다(토큰 정의 일원화).
# 호출 시점에 읽으므로 .env/환경 갱신도 반영된다.

def searchad_creds() -> tuple[str, str, str]:
    """네이버 검색광고 키워드도구 자격증명 (API_KEY, SECRET_KEY, CUSTOMER_ID)."""
    return (os.environ.get("NAVER_AD_API_KEY", ""),
            os.environ.get("NAVER_AD_SECRET_KEY", ""),
            os.environ.get("NAVER_AD_CUSTOMER_ID", ""))


def searchad_available() -> bool:
    return all(searchad_creds())


def naver_map_creds() -> tuple[str, str]:
    """네이버 클라우드 Maps 자격증명 (KEY_ID, KEY)."""
    return (os.environ.get("NAVER_MAP_KEY_ID", ""),
            os.environ.get("NAVER_MAP_KEY", ""))


def naver_map_available() -> bool:
    return all(naver_map_creds())


def sgis_creds() -> tuple[str, str]:
    """SGIS 통계지리정보 자격증명 (CONSUMER_KEY, CONSUMER_SECRET)."""
    return (os.environ.get("SGIS_CONSUMER_KEY", ""),
            os.environ.get("SGIS_CONSUMER_SECRET", ""))


def sgis_available() -> bool:
    return all(sgis_creds())
