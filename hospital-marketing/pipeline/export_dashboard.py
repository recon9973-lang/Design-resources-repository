#!/usr/bin/env python3
"""대시보드용 실데이터 JSON 내보내기.

반경 4단계(500m~2km) 각각의 경쟁·점수 지표와 키워드 노출, 지도 점 데이터를
../data/dashboard-live.json으로 저장한다. 대시보드(index.html)는 이 파일이
있으면 목업 대신 실데이터를 표시한다.

사용:
    python3 export_dashboard.py --name 라메스피부과의원 --lat 37.5079 --lng 127.0382
"""

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import config, scoring                                     # noqa: E402
from medirank.connectors import hira, naver_content, naver_local, sgis   # noqa: E402
from medirank.geo import haversine_m                                     # noqa: E402

DEFAULT_KEYWORDS = [
    "강남 피부과", "역삼역 피부과", "테헤란로 피부과", "강남 리프팅",
    "강남 보톡스", "강남 여드름치료", "강남 탈모치료", "강남 기미레이저",
]

KEYWORD_TYPE = {  # 표시용 간이 분류
    "강남 피부과": "지역+진료과", "역삼역 피부과": "지역+진료과",
    "테헤란로 피부과": "지역+진료과", "강남 리프팅": "시술",
    "강남 보톡스": "시술", "강남 여드름치료": "고민",
    "강남 탈모치료": "고민", "강남 기미레이저": "시술",
}


def bearing_deg(lat1, lng1, lat2, lng2) -> float:
    """중심→대상 방위각(도). 지도 점 배치용."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--address", default="")
    ap.add_argument("--department", default="피부과")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lng", type=float, required=True)
    ap.add_argument("--dgsbjt", default="14")
    ap.add_argument("--adm-cd", default="11230")
    ap.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    ap.add_argument("--out", default=str(Path(__file__).parent.parent / "data" / "dashboard-live.json"))
    args = ap.parse_args()

    if not (config.hira_available() and config.naver_available()):
        print("경고: 실키 없음 — 일부 목업 대체")

    # 1) 최대 반경(2km) 한 번만 조회 후 반경별로 잘라 쓴다
    print("[1/3] HIRA 반경 2km 조회 ...")
    hospitals = hira.fetch_hospitals_radius(args.lng, args.lat, 2000, args.dgsbjt, rows=1000)
    all_comps = []
    for h in hospitals:
        if h["name"] == args.name or h.get("latitude") is None:
            continue
        d = haversine_m(args.lat, args.lng, h["latitude"], h["longitude"])
        if d > 2000:
            continue
        all_comps.append({
            "name": h["name"], "distance_m": round(d, 1),
            "bearing_deg": round(bearing_deg(args.lat, args.lng, h["latitude"], h["longitude"]), 1),
            "same_department": True,
        })
    all_comps.sort(key=lambda c: c["distance_m"])
    print(f"    2km 내 {len(all_comps)}곳")

    # 2) 키워드 노출 (반경 무관 공통)
    print("[2/3] 네이버 키워드 노출 ...")
    kw_results = []
    for kw in args.keywords:
        r = naver_local.keyword_exposure(kw, args.name)
        grade = ("top" if r["exposed"] and r["rank"] and r["rank"] <= 3
                 else "mid" if r["exposed"] else "none")
        kw_results.append({
            "kw": kw, "type": KEYWORD_TYPE.get(kw, "기타"),
            "api": r["exposed"], "pos": r["rank"], "grade": grade,
            "comp": r["results"][0]["title"] if r["results"] else "-",
            "content": naver_content.content_exposure(kw, args.name),
        })
    exposure = scoring.exposure_score(
        [{"exposed": k["api"], "rank": k["pos"]} for k in kw_results])

    # 3) SGIS + 반경별 점수
    print("[3/3] SGIS + 반경별 점수 ...")
    stats = sgis.area_stats(args.adm_cd)
    dnsty = stats["ppltn_dnsty"] if stats and stats.get("ppltn_dnsty") else 13000.0
    place = scoring.place_quality_score(154, 4.4, 44, 0.7)  # TODO: 플레이스 실측

    radius_data = {}
    for r_m in config.VALID_RADII_M:
        comps = [c for c in all_comps if c["distance_m"] <= r_m]
        density = scoring.density_score(comps, r_m)
        pop = int(dnsty * math.pi * (r_m / 1000) ** 2)
        demand = scoring.demand_score(pop, 0.45, r_m)
        final = scoring.final_score(exposure, density, demand, place)
        radius_data[str(r_m)] = {
            "score": round(final), "competitors": len(comps),
            "comp": {"exposure": round(exposure), "density": round(density),
                     "demand": round(demand), "place": round(place)},
        }
        print(f"    반경 {r_m}m: 경쟁 {len(comps)}곳 · 최종 {final}점")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "month": date.today().strftime("%Y-%m"),
        "live": True,
        "hospital": {"name": args.name, "department": args.department,
                     "address": args.address},
        "radius_data": radius_data,
        "keywords": kw_results,
        "map_points": all_comps[:400],
        "sgis": stats,
        "data_basis": {
            "sources": ["건강보험심사평가원 병원정보서비스",
                        "네이버 지역 검색 API (비로그인, 상위 5건)",
                        "SGIS 통계지리정보"],
            "note": "미노출 = 공식 API 상위 5위 밖. 실제 검색화면과 다를 수 있음.",
        },
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
