"""월간 리포트 지표 빌더 — 커넥터와 점수 엔진을 묶어 대시보드/PDF용 JSON 생성.

고객에게 안전한 요약값만 담는다. 원본 API payload는 결과에 포함하지 않는다
(internal_evidence_items로 별도 보관 대상).
"""

from datetime import date

from . import config, scoring
from .connectors import hira, naver_local
from .geo import within_radius


def grade_of(result: dict) -> str:
    """고객 화면용 등급: top(3위 이내) / mid(노출) / not_exposed."""
    if not result.get("exposed"):
        return "not_exposed"
    if result.get("rank") is not None and result["rank"] <= 3:
        return "top"
    return "mid"


def build_monthly_metrics(
    my_hospital: dict,
    keywords: list[str],
    radius_m: int = 1000,
    population_in_radius: int = 42000,
    target_age_ratio: float = 0.45,
    place_profile: dict | None = None,
    month: str | None = None,
) -> dict:
    """병원 1곳의 월간 지표 계산 (1단계 내부 리포트 MVP의 코어).

    my_hospital: name/latitude/longitude/department_name 필수.
    place_profile: review_count/rating/photo_count/info_completeness.
    population/age 값은 SGIS 연동 전까지 호출자가 주입한다.
    """
    if radius_m not in config.VALID_RADII_M:
        raise ValueError(f"radius_m must be one of {config.VALID_RADII_M}")

    place_profile = place_profile or {
        "review_count": 154, "rating": 4.4, "photo_count": 44,
        "info_completeness": 0.7,
    }

    # 1) 반경 내 경쟁 병원 (HIRA 마스터 기준)
    all_hospitals = hira.fetch_hospitals()
    competitors = within_radius(my_hospital, all_hospitals, radius_m)
    my_dept = my_hospital.get("department_name", "")
    for c in competitors:
        c["same_department"] = (c.get("department_name") == my_dept)

    # 2) 키워드별 공식 API 노출
    keyword_results = [
        naver_local.keyword_exposure(kw, my_hospital["name"]) for kw in keywords
    ]

    # 3) 점수
    exposure = scoring.exposure_score(keyword_results)
    density = scoring.density_score(competitors, radius_m)
    demand = scoring.demand_score(population_in_radius, target_age_ratio, radius_m)
    place = scoring.place_quality_score(**place_profile)
    final = scoring.final_score(exposure, density, demand, place)

    weak = [r["keyword"] for r in keyword_results if not r["exposed"]]

    return {
        "month": month or date.today().strftime("%Y-%m"),
        "hospital": my_hospital["name"],
        "radius_m": radius_m,
        "final_marketing_score": final,
        "scores": {
            "exposure": exposure, "density": density,
            "demand": demand, "place_quality": place,
        },
        "competitor_count": len(competitors),
        "competitors_nearest": [
            {"name": c["name"], "distance_m": c["distance_m"],
             "same_department": c["same_department"]}
            for c in competitors[:10]
        ],
        "keywords": [
            {"keyword": r["keyword"], "api_exposed": r["exposed"],
             "api_rank": r["rank"], "grade": grade_of(r)}
            for r in keyword_results
        ],
        "weak_keywords": weak,
        "data_basis": {
            "sources": [
                "건강보험심사평가원 병원정보서비스",
                "네이버 지역 검색 API (비로그인, display 최대 5)",
            ],
            "mock_mode": not (config.hira_available() and config.naver_available()),
            "disclaimer": (
                "공식 API 결과는 실제 검색화면과 다를 수 있습니다. "
                "인구·상권 지표는 잠재 수요 참고지표입니다."
            ),
        },
    }
