#!/usr/bin/env python3
"""0단계 실데이터 검증 — 세 데이터 소스를 실키로 호출해 파이프라인을 끝까지 돌린다.

검증 항목 (기획서 13.1):
  1. HIRA 반경 검색으로 경쟁 병원 목록 생성 + 하버사인 거리 교차 확인
  2. 네이버 지역 검색 API로 키워드별 내 병원 노출 여부/위치 확인
  3. SGIS 인구 지표 조회
  4. 점수 엔진으로 최종 마케팅 경쟁력 점수 산출

사용:
    python3 validate_stage0.py                          # 기본: 역삼역 인근 샘플
    python3 validate_stage0.py --name "OO피부과의원" --lat 37.5 --lng 127.03
    python3 validate_stage0.py --out result.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import config, scoring                       # noqa: E402
from medirank.connectors import hira, naver_local, sgis    # noqa: E402
from medirank.geo import haversine_m                       # noqa: E402

DGSBJT_DERMA = "14"  # 피부과

DEFAULT_KEYWORDS = [
    "강남 피부과", "역삼역 피부과", "테헤란로 피부과", "강남 리프팅",
    "강남 보톡스", "강남 여드름치료", "강남 탈모치료", "강남 기미레이저",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="강남고운세상피부과의원", help="내 병원명 (HIRA 등록명)")
    ap.add_argument("--lat", type=float, default=37.4989, help="병원 위도")
    ap.add_argument("--lng", type=float, default=127.0293, help="병원 경도")
    ap.add_argument("--radius", type=int, default=1000, choices=[500, 1000, 1500, 2000])
    ap.add_argument("--dgsbjt", default=DGSBJT_DERMA, help="진료과목코드 (기본 14=피부과)")
    ap.add_argument("--adm-cd", default="11230", help="SGIS 행정구역코드 (기본 강남구)")
    ap.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    live = config.hira_available() and config.naver_available()
    print(f"[모드] HIRA={'실키' if config.hira_available() else '목업'} · "
          f"NAVER={'실키' if config.naver_available() else '목업'} · "
          f"SGIS={'실키' if sgis.available() else '없음(기본값 사용)'}")
    if not live:
        print("경고: 실키가 없어 목업으로 동작합니다. .env를 확인하세요.")

    # 1) HIRA 반경 검색 -----------------------------------------------------
    print(f"\n[1/4] HIRA 반경 {args.radius}m 병원 조회 (진료과목 {args.dgsbjt}) ...")
    hospitals = hira.fetch_hospitals_radius(args.lng, args.lat, args.radius, args.dgsbjt)
    competitors = []
    dist_mismatch = 0
    for h in hospitals:
        if h["name"] == args.name or h.get("latitude") is None:
            continue
        d = haversine_m(args.lat, args.lng, h["latitude"], h["longitude"])
        if d > args.radius * 1.10:  # 서버 반경과 하버사인 10% 초과 불일치
            dist_mismatch += 1
        competitors.append({
            "name": h["name"], "address": h["address"],
            "distance_m": round(d, 1), "same_department": True,
        })
    competitors.sort(key=lambda c: c["distance_m"])
    print(f"    경쟁 병원 {len(competitors)}곳 · 거리 교차검증 불일치 {dist_mismatch}건")
    for c in competitors[:5]:
        print(f"    - {c['distance_m']:7.1f}m  {c['name']}")

    # 2) 네이버 키워드 노출 --------------------------------------------------
    print(f"\n[2/4] 네이버 지역 검색 노출 확인 — 키워드 {len(args.keywords)}개 ...")
    keyword_results = []
    for kw in args.keywords:
        r = naver_local.keyword_exposure(kw, args.name)
        keyword_results.append(r)
        mark = f"노출 {r['rank']}위" if r["exposed"] else "미노출"
        top = r["results"][0]["title"] if r["results"] else "-"
        print(f"    - {kw}: {mark} (1위: {top})")

    # 3) SGIS 인구/상권 -------------------------------------------------------
    print(f"\n[3/4] SGIS 인구/상권 조회 (adm_cd={args.adm_cd}) ...")
    import math
    stats = sgis.area_stats(args.adm_cd)
    if stats and stats.get("ppltn_dnsty"):
        area_km2 = math.pi * (args.radius / 1000) ** 2
        population_in_radius = int(stats["ppltn_dnsty"] * area_km2)
        print(f"    {stats['adm_nm']} ({stats['year']}년) — 총인구 {stats['tot_ppltn']:,.0f}명 · "
              f"밀도 {stats['ppltn_dnsty']:,.0f}명/km² · 평균연령 {stats['avg_age']}세")
        if stats.get("employee_cnt"):
            print(f"    종사자 {stats['employee_cnt']:,.0f}명 · 사업체 {stats['corp_cnt']:,.0f}곳 (상권 참고지표)")
        print(f"    반경 {args.radius}m 환산(밀도 기반): 약 {population_in_radius:,}명")
    else:
        population_in_radius = 42000
        print("    SGIS 조회 실패 — 기본값 42,000명 사용")

    # 4) 점수 계산 -----------------------------------------------------------
    print("\n[4/4] 점수 계산 ...")
    exposure = scoring.exposure_score(keyword_results)
    density = scoring.density_score(competitors, args.radius)
    demand = scoring.demand_score(population_in_radius, 0.45, args.radius)
    place = None  # 플레이스 지표(리뷰·평점·사진): 공식 API 미제공 → 미측정(조작값 금지)
    comp = scoring.composite_measured(exposure=exposure, density=density, demand=demand, place=place)
    final = comp["score"]

    summary = {
        "hospital": args.name,
        "radius_m": args.radius,
        "live_mode": live,
        "competitor_count": len(competitors),
        "distance_crosscheck_mismatch": dist_mismatch,
        "keywords_exposed": sum(1 for r in keyword_results if r["exposed"]),
        "keywords_total": len(keyword_results),
        "population_in_radius_est": population_in_radius,
        "scores": {"exposure": exposure, "density": density,
                   "demand": demand, "place_quality": place},  # None = 미측정
        "final_marketing_score": final,
        "score_measured_weight_pct": comp["measured_weight_pct"],
        "note": "플레이스 품질 지표(리뷰/평점/사진)는 공식 API 미제공으로 미측정 — 종합점수에서 제외",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary,
                        "competitors": competitors,
                        "keywords": [{k: v for k, v in r.items() if k != "results"}
                                     for r in keyword_results]},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"\n상세 결과 저장: {args.out}")


if __name__ == "__main__":
    main()
