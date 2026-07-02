#!/usr/bin/env python3
"""개원 입지 후보지 비교 분석 — 베놈 내부 컨설팅 도구.

후보지별로 반경 내 같은 진료과 경쟁 현황(HIRA)과 인구·상권 지표(SGIS)를
수집해 "입지 참고 점수"를 계산하고, 입지 비교 화면용 JSON을 내보낸다.

입지 참고 점수(0-100, 당사 산식) = 경쟁 여유 50% + 잠재 수요 30% + 상권 활동 20%
- 경쟁 여유: 기존 경쟁 밀도 점수(캘리브레이션 동일)
- 잠재 수요: 반경 인구 기반 demand_score
- 상권 활동: 구 단위 종사자 밀도 기반 (직장인 유동 근사)

⚠️ 참고 지표입니다. 임대료, 접근성(역세권/주차), 건물 조건, 인허가 등
입지 결정의 핵심 변수를 포함하지 않으며 개원 성과를 보장하지 않습니다.

사용:
    python3 compare_sites.py --dgsbjt 14 \
      --site "강남 역삼,37.5006,127.0364,11230" \
      --site "마포 홍대입구,37.5563,126.9236,11140" \
      --site "노원 상계,37.6543,127.0568,11110"
"""

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import config, scoring                # noqa: E402
from medirank.connectors import hira, sgis          # noqa: E402
from medirank.geo import haversine_m                # noqa: E402

WEIGHTS = {"competition": 0.5, "demand": 0.3, "commerce": 0.2}


def commerce_score(emp_density_per_km2: float) -> float:
    """상권 활동 점수 — 구 단위 종사자 밀도 기반 근사 (0-100).

    서울 구별 종사자 밀도 중위값 ~25,000명/km² 수준에서 63점이 되는 완만한 곡선.
    """
    return round(100.0 * (1.0 - math.exp(-emp_density_per_km2 / 25000.0)), 1)


def analyze_site(name: str, lat: float, lng: float, adm_cd: str,
                 dgsbjt: str, radius_m: int) -> dict:
    hospitals = hira.fetch_hospitals_radius(lng, lat, radius_m, dgsbjt, rows=1000)
    comps = []
    for h in hospitals:
        if h.get("latitude") is None:
            continue
        d = haversine_m(lat, lng, h["latitude"], h["longitude"])
        if d <= radius_m:
            comps.append({"distance_m": d, "same_department": True})

    density = scoring.density_score(comps, radius_m)

    stats = sgis.area_stats(adm_cd) or {}
    dnsty = stats.get("ppltn_dnsty") or 13000.0
    area_km2 = math.pi * (radius_m / 1000.0) ** 2
    pop_radius = int(dnsty * area_km2)
    demand = scoring.demand_score(pop_radius, 0.45, radius_m)

    # 구 면적 = 총인구/밀도 → 종사자 밀도(명/km²)
    emp_density = 0.0
    if stats.get("employee_cnt") and stats.get("tot_ppltn") and dnsty:
        gu_area = stats["tot_ppltn"] / dnsty
        emp_density = stats["employee_cnt"] / gu_area if gu_area else 0.0
    commerce = commerce_score(emp_density)

    site_score = round(WEIGHTS["competition"] * density
                       + WEIGHTS["demand"] * demand
                       + WEIGHTS["commerce"] * commerce, 1)
    per10k = round(len(comps) / (pop_radius / 10000.0), 1) if pop_radius else None

    return {
        "name": name, "lat": lat, "lng": lng,
        "adm_cd": adm_cd, "adm_nm": stats.get("adm_nm"),
        "competitors": len(comps),
        "competition_score": density,
        "population_radius": pop_radius,
        "demand_score": demand,
        "avg_age": stats.get("avg_age"),
        "employee_density_km2": round(emp_density),
        "commerce_score": commerce,
        "saturation_per_10k": per10k,   # 반경 인구 1만명당 경쟁 병원 수 (낮을수록 여유)
        "site_score": site_score,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", action="append", required=True,
                    metavar="이름,위도,경도,SGIS행정코드",
                    help='예: "강남 역삼,37.5006,127.0364,11230" (반복 지정)')
    ap.add_argument("--dgsbjt", default="14", help="진료과목코드 (기본 14=피부과)")
    ap.add_argument("--department", default="피부과")
    ap.add_argument("--radius", type=int, default=1000, choices=[500, 1000, 1500, 2000])
    ap.add_argument("--out", default=str(Path(__file__).parent.parent / "data" / "location-live.json"))
    args = ap.parse_args()

    if not config.hira_available():
        print("경고: HIRA 실키 없음 — 목업 데이터로는 입지 비교가 무의미합니다.")

    sites = []
    for spec in args.site:
        name, lat, lng, adm = [s.strip() for s in spec.split(",")]
        print(f"[분석] {name} ...")
        s = analyze_site(name, float(lat), float(lng), adm, args.dgsbjt, args.radius)
        sites.append(s)
        print(f"    경쟁 {s['competitors']}곳 · 포화도 {s['saturation_per_10k']}/만명 · "
              f"입지 참고 점수 {s['site_score']}")

    sites.sort(key=lambda s: s["site_score"], reverse=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "month": date.today().strftime("%Y-%m"),
        "department": args.department,
        "radius_m": args.radius,
        "weights": WEIGHTS,
        "sites": sites,
        "disclaimer": ("입지 참고 점수는 당사 산식에 따른 참고 지표이며 임대료·접근성·건물 조건 등 "
                       "핵심 입지 변수를 포함하지 않습니다. 개원 성과를 보장하지 않습니다."),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
