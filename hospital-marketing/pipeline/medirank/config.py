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

# 네이버 검색광고(광고주) API — 절대 검색량·연관검색어 (키워드도구)
NAVER_AD_API_KEY = os.environ.get("NAVER_AD_API_KEY", "")
NAVER_AD_SECRET_KEY = os.environ.get("NAVER_AD_SECRET_KEY", "")
NAVER_AD_CUSTOMER_ID = os.environ.get("NAVER_AD_CUSTOMER_ID", "")

VALID_RADII_M = (500, 1000, 1500, 2000)


def hira_available() -> bool:
    return bool(DATA_GO_KR_SERVICE_KEY)


def naver_available() -> bool:
    return bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)


def searchad_available() -> bool:
    return bool(NAVER_AD_API_KEY and NAVER_AD_SECRET_KEY and NAVER_AD_CUSTOMER_ID)
