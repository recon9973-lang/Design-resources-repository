"""마케팅 경쟁력 점수 엔진 (기획서 6장).

최종 점수 = 노출 40% + 경쟁 밀도 25% + 수요/입지 20% + 플레이스 품질 15%.
각 세부 점수는 0-100. 계수는 초기 캘리브레이션 값이며 0단계 검증(샘플 3개
지역)에서 실제 분포를 보고 조정한다.
"""

import math

WEIGHTS = {"exposure": 0.40, "density": 0.25, "demand": 0.20, "place": 0.15}


def exposure_score(keyword_results: list[dict]) -> float:
    """노출 점수 (40%).

    키워드별: API 결과 3위 이내 100 / 5위 이내 75 / 노출 55 / 미노출 0.
    스냅샷 참고 순위는 별도 표기만 하고 점수에는 반영하지 않는다
    (공식 API 지표와 분리 원칙, 기획서 6.1).
    """
    if not keyword_results:
        return 0.0
    pts = []
    for r in keyword_results:
        if not r.get("exposed"):
            pts.append(0)
        elif r.get("rank") is not None and r["rank"] <= 3:
            pts.append(100)
        elif r.get("rank") is not None and r["rank"] <= 5:
            pts.append(75)
        else:
            pts.append(55)
    return round(sum(pts) / len(pts), 1)


def competition_weight(distance_m: float, same_department: bool,
                       radius_m: int, top_exposed: bool = False) -> float:
    """경쟁 병원 1곳의 가중치 (기획서 6.2 산식).

    - 가까울수록 높음: 반경 대비 선형 감쇠 (경계에서 0.2, 중심에서 1.0)
    - 같은 진료과 1.0 / 유사 진료과 0.65
    - 같은 키워드 상위 노출 병원 1.5배
    """
    proximity = 1.0 - 0.8 * min(distance_m / radius_m, 1.0)
    dept = 1.0 if same_department else 0.65
    boost = 1.5 if top_exposed else 1.0
    return round(proximity * dept * boost, 3)


def density_score(competitors: list[dict], radius_m: int) -> float:
    """경쟁 밀도 점수 (25%). 가중 경쟁량이 적을수록 높다.

    로그 압축 매핑: score = max(0, 100 - 18·ln(1 + W_eq)).
    W_eq는 반경 면적으로 정규화한 1km 환산 가중 경쟁량.

    계수 18은 2026-07 HIRA 실측(반경 1km 피부과 표방 기준) 캘리브레이션:
      강남 역삼 298곳 W=114 → 14.5점 / 마포 홍대 74곳 W=56 → 27.1점 /
      청주 성안길 19곳 W=13 → 53.0점 / 경쟁 0곳 → 100점.
    지수식(exp(-W/K))은 과밀 상권에서 0점으로 포화되어 변별력이 없어 교체.
    """
    total_w = sum(
        competition_weight(
            c["distance_m"], c.get("same_department", True), radius_m,
            c.get("top_exposed", False),
        )
        for c in competitors
    )
    w_eq = total_w / ((radius_m / 1000.0) ** 2)
    return round(max(0.0, 100.0 - 18.0 * math.log1p(w_eq)), 1)


def demand_score(population_in_radius: int, target_age_ratio: float,
                 radius_m: int) -> float:
    """수요/입지 점수 (20%) — 잠재 수요 참고지표.

    반경 면적 대비 인구 밀도와 타깃 연령 비중을 결합한다.
    기준 밀도 15,000명/km²(서울 주요 상권 중위값 근사)에서 70점.
    """
    area_km2 = math.pi * (radius_m / 1000.0) ** 2
    density = population_in_radius / area_km2 if area_km2 else 0.0
    base = 100.0 * (1.0 - math.exp(-density / 15000.0))
    age_factor = 0.7 + 0.6 * min(max(target_age_ratio, 0.0), 1.0)  # 0.7~1.3
    return round(min(base * age_factor, 100.0), 1)


def place_quality_score(review_count: int, rating: float, photo_count: int,
                        info_completeness: float,
                        peer_median_reviews: int = 200,
                        peer_median_photos: int = 80) -> float:
    """플레이스 품질 점수 (15%). 리뷰 본문은 사용하지 않는다 (공개 집계값만).

    - 리뷰 수 30%: 주변 경쟁 중위값 대비 (상한 1.0)
    - 평점 25%: 3.0 이하 0점, 5.0 만점 선형
    - 사진 수 20%: 중위값 대비
    - 기본정보 완성도 25%: 영업시간/예약/전화/주차 등 채움 비율(0-1)
    """
    reviews = min(review_count / peer_median_reviews, 1.0) if peer_median_reviews else 0
    rate = min(max((rating - 3.0) / 2.0, 0.0), 1.0)
    photos = min(photo_count / peer_median_photos, 1.0) if peer_median_photos else 0
    info = min(max(info_completeness, 0.0), 1.0)
    return round(100.0 * (0.30 * reviews + 0.25 * rate + 0.20 * photos + 0.25 * info), 1)


def final_score(exposure: float, density: float, demand: float, place: float) -> float:
    """가중 합산 최종 점수 (0-100)."""
    total = (WEIGHTS["exposure"] * exposure + WEIGHTS["density"] * density
             + WEIGHTS["demand"] * demand + WEIGHTS["place"] * place)
    return round(total, 1)


def composite_measured(exposure=None, density=None, demand=None, place=None) -> dict:
    """측정된 축만으로 종합 점수 산정 — 가중치 재정규화. 미측정(None) 축은 제외.

    신뢰도 원칙: 실측값이 없는 축은 임의값으로 채우지 않고 제외하고, 산정에
    실제 반영된 가중 비중(measured_weight_pct)을 함께 반환해 투명하게 고지한다.
    반환: {score(0-100|None), measured_weight_pct, components{축:값|None}}
    """
    vals = {"exposure": exposure, "density": density, "demand": demand, "place": place}
    avail = {k: v for k, v in vals.items() if v is not None}
    tw = sum(WEIGHTS[k] for k in avail)
    score = round(sum(WEIGHTS[k] * v for k, v in avail.items()) / tw, 1) if tw else None
    return {"score": score, "measured_weight_pct": round(tw * 100), "components": vals}
