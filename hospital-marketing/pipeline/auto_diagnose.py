#!/usr/bin/env python3
"""업체명 하나로 끝내는 자동 진단.

업체명 → 업체 특정(주소·업종) → 키워드 자동 산출(동/구/시/메인 ×
업종·진료과·시술) → 키워드별 플레이스 + 통합검색 6개 영역 노출 진단.

사용:
    python3 auto_diagnose.py --name 베놈 --region 대구
    python3 auto_diagnose.py --name 라메스피부과의원
    python3 auto_diagnose.py --name 베놈 --region 대구 --out ../data/auto-베놈.json
"""

import argparse
import html as html_mod
import json
import math
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import config, keywordgen, scoring                  # noqa: E402
from medirank.connectors import hira, naver_content, naver_local, naver_place, searchad, staticmap, sgis  # noqa: E402
from medirank.geo import haversine_m                              # noqa: E402

SEC_KEYS = ["blog", "cafe", "web", "news", "image", "kin"]
SEC_LABELS = ["블로그", "카페", "웹문서", "뉴스", "이미지", "지식iN"]


def cell(c: dict) -> str:
    if not c or c.get("exposed") is None:
        return "미검증"
    if not c.get("present"):
        return "—"
    return f"○{c['position']}" if c.get("exposed") else "✕"


def analyze_location(biz: dict, region: dict, cls: dict, radius_m: int = 1000) -> dict | None:
    """업체 좌표·행정구역 기반 입지 참고 분석.

    - 공통: SGIS 인구·종사자 지표 → 잠재 수요·상권 활동 점수
    - 병원: HIRA 같은 진료과 반경 경쟁 → 경쟁 여유 점수 + 입지 참고 점수
    - 비병원: 경쟁 지표는 공식 데이터가 없어 수요·상권만 제공
    """
    lat, lng = biz.get("latitude"), biz.get("longitude")
    adm_cd = sgis.find_adm_cd(region.get("city") or "", region.get("gu"))
    stats = sgis.area_stats(adm_cd) if adm_cd else None
    if not stats and lat is None:
        return None

    out = {"radius_m": radius_m, "adm_cd": adm_cd,
           "adm_nm": (stats or {}).get("adm_nm"),
           "avg_age": (stats or {}).get("avg_age")}

    # 체감(인구가중) 밀도 우선 — 넓은 시의 외곽 읍·면이 도심 밀도를 희석하는 문제 보정.
    avg_dnsty = (stats or {}).get("ppltn_dnsty")
    w_dnsty = sgis.pop_weighted_density(adm_cd) if adm_cd else None
    dnsty = w_dnsty or avg_dnsty or 13000.0
    out["density_basis"] = "체감(인구가중)" if w_dnsty else ("시·구 평균" if avg_dnsty else "기본값")
    pop_radius = int(dnsty * math.pi * (radius_m / 1000.0) ** 2)
    demand = scoring.demand_score(pop_radius, 0.45, radius_m)
    out.update({"population_radius": pop_radius, "demand_score": demand})

    # 종사자 밀도도 같은 체감 기준으로 스케일 (기하평균 종사자밀도 × 체감/평균 배율)
    emp_density = 0.0
    if stats and stats.get("employee_cnt") and stats.get("tot_ppltn") and avg_dnsty:
        real_area = stats["tot_ppltn"] / avg_dnsty
        emp_base = stats["employee_cnt"] / real_area if real_area else 0.0
        emp_density = emp_base * (dnsty / avg_dnsty)
    commerce = round(100.0 * (1.0 - math.exp(-emp_density / 25000.0)), 1)
    out.update({"employee_density_km2": round(emp_density), "commerce_score": commerce})

    dgsbjt = keywordgen.DEPT_DGSBJT.get(cls.get("dept") or "")
    if cls.get("is_hospital") and dgsbjt and lat is not None and lng is not None:
        # 한 번만 최대 반경으로 조회하고, 거리로 걸러 반경별 지표를 산출(호출 절약).
        radii = sorted(set(config.VALID_RADII_M) | {radius_m})
        rmax = max(radii)
        hospitals = hira.fetch_hospitals_radius(lng, lat, rmax, dgsbjt, rows=1000)
        comp_full = sorted(
            ({"name": h.get("name"),
              "distance_m": haversine_m(lat, lng, h["latitude"], h["longitude"])}
             for h in hospitals if h.get("latitude") is not None),
            key=lambda c: c["distance_m"])
        comp_full = [c for c in comp_full if c["distance_m"] <= rmax]

        breakdown = {}
        for r in radii:
            comps_r = [{"distance_m": c["distance_m"], "same_department": True}
                       for c in comp_full if c["distance_m"] <= r]
            pop_r = int(dnsty * math.pi * (r / 1000.0) ** 2)
            breakdown[r] = {
                "competitors": len(comps_r),
                "competition_score": scoring.density_score(comps_r, r),
                "demand_score": scoring.demand_score(pop_r, 0.45, r),
                "population_radius": pop_r,
            }
        cur = breakdown[radius_m]
        density = cur["competition_score"]
        site = round(0.5 * density + 0.3 * demand + 0.2 * commerce, 1)
        out.update({
            "competitors": cur["competitors"], "competition_score": density,
            "saturation_per_10k": round(cur["competitors"] / (pop_radius / 10000.0), 1) if pop_radius else None,
            "site_score": site,
            "radius_breakdown": breakdown,
            # 실측 경쟁 벤치마크용 — 가장 가까운 동일 진료과 경쟁 병원(이름·거리)
            "competitor_list": [{"name": c["name"], "distance_m": round(c["distance_m"])}
                                for c in comp_full[:8] if c.get("name")],
        })
    return out


def build_actions(rows: list, location: dict | None, place_info: dict | None,
                  benchmark: dict | None) -> list[dict]:
    """측정된 갭에서 우선 개선 액션을 구조화 산출(기획서 action_recommendations).

    반환 각 항목: {priority, action_type, title, explanation, evidence_metric,
    compliance_status}. 액션은 전부 운영·정보 정비 중심이라 compliance=safe
    (치료효과·후기·전후·보장 표현 배제 — 의료광고 안전).
    """
    acts = []
    noexp = [r["kw"] for r in rows if not (r["place"]["exposed"]
             or any((r["content"].get(s) or {}).get("exposed") for s in SEC_KEYS))]
    blog_ex = sum(1 for r in rows if (r["content"].get("blog") or {}).get("exposed"))
    cafe_ex = sum(1 for r in rows if (r["content"].get("cafe") or {}).get("exposed"))

    if noexp:
        acts.append({
            "action_type": "keyword_coverage",
            "title": f"미노출 키워드 {len(noexp)}개 커버리지 정비",
            "explanation": (f'"{"·".join(noexp[:3])}" 등이 어느 영역에도 노출되지 않습니다. '
                            "실제 제공하는 진료라면 소개·진료항목·FAQ·블로그에 반영하세요."),
            "evidence_metric": f"키워드 {len(rows)}개 중 {len(noexp)}개 전 영역 미노출",
            "compliance_status": "safe"})
    if blog_ex + cafe_ex <= max(1, len(rows) // 6):
        acts.append({
            "action_type": "content_gap",
            "title": "비교·검증 콘텐츠(블로그·카페) 보강",
            "explanation": ("환자가 비교·검증하는 자리(블로그·카페)에 우리 콘텐츠가 거의 없습니다. "
                            "광고성 글 대신 진료 사례 기반 스토리텔링으로 채우세요."),
            "evidence_metric": f"블로그·카페 노출 {blog_ex + cafe_ex}건",
            "compliance_status": "safe"})
    if isinstance(benchmark, dict) and benchmark:
        weak = []
        for s, label in zip(SEC_KEYS, SEC_LABELS):
            active = sum(1 for r in rows if (r["content"].get(s) or {}).get("present"))
            exposed = sum(1 for r in rows if (r["content"].get(s) or {}).get("exposed"))
            ours = round(100 * exposed / active) if active else 0
            comp = benchmark.get(s) or 0
            if comp - ours >= 25:
                weak.append((label, ours, comp))
        if weak:
            weak.sort(key=lambda w: w[2] - w[1], reverse=True)
            label, ours, comp = weak[0]
            acts.append({
                "action_type": "benchmark_gap",
                "title": f"{label} 영역 — 경쟁 대비 격차 축소",
                "explanation": (f"{label}에서 우리 노출률 {ours}%로 상위 경쟁군 평균 {comp}%보다 낮습니다. "
                                "이 영역 콘텐츠를 우선 보강해 격차를 좁히세요."),
                "evidence_metric": f"{label} 노출률 우리 {ours}% vs 경쟁 {comp}%",
                "compliance_status": "safe"})
    acts.append({
        "action_type": "place_ops",
        "title": "플레이스 기본정보·리뷰 운영 정비",
        "explanation": ("대표 사진·진료시간·주차·예약 링크를 실제 운영 상태와 맞추고, "
                        "자발적 리뷰 동선과 답글 운영을 정비하세요."),
        "evidence_metric": (f"기본정보 완성도 {int((place_info.get('info_completeness') or 0)*100)}%"
                            if place_info else "플레이스 직접 입력·운영 기준(리뷰 본문 미수집)"),
        "compliance_status": "safe"})

    acts = acts[:4]
    for i, a in enumerate(acts):
        a["priority"] = i + 1
    return acts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="업체명 (이것만 있으면 됨)")
    ap.add_argument("--region", default=None, help="동명 업체 구분용 지역 힌트 (예: 대구, 수성구)")
    ap.add_argument("--max-keywords", type=int, default=12)
    ap.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    ap.add_argument("--html", default=None, help="공유용 리포트 HTML 저장 경로 (전체판)")
    ap.add_argument("--html-masked", default=None,
                    help="마킹판 HTML 저장 경로 — 세부 수치를 산출 단계에서 제외(소스에도 없음), "
                         "무료 회원 열람 시 전체판 제공 모델용")
    ap.add_argument("--no-location", action="store_true", help="입지 분석 생략")
    ap.add_argument("--benchmark-competitors", type=int, default=3,
                    help="실측 경쟁 벤치마크에 쓸 상위 경쟁 병원 수 (0=끄기, API 호출 증가)")
    ap.add_argument("--no-benchmark", action="store_true", help="경쟁 벤치마크 측정 생략")
    ap.add_argument("--reviews", type=int, default=None,
                    help="플레이스 리뷰 수(병원이 자기 플레이스에서 확인해 입력) — 실측")
    ap.add_argument("--rating", type=float, default=None, help="플레이스 평점 0-5 (직접 입력)")
    ap.add_argument("--photos", type=int, default=None, help="플레이스 사진 수 (직접 입력)")
    ap.add_argument("--place-url", default=None, help="네이버 플레이스 URL (내부 집계 수집용)")
    ap.add_argument("--collect-place-metrics", action="store_true",
                    help="플레이스 공개 집계값 자동 수집(내부·대면 전용·법무 검토 전·미검수). 개방망 필요")
    args = ap.parse_args()

    # 1) 업체 특정
    res = keywordgen.resolve_business(args.name, args.region)
    if not res["chosen"]:
        print(f"업체를 찾지 못했습니다: {args.name} ({res['note']})")
        sys.exit(1)
    biz = res["chosen"]
    # 도로명주소(도·시 포함)와 지번주소(법정동 포함)를 합쳐 지역축을 잡는다.
    # 네이버 지번주소엔 도(강원 등)가 빠져 '춘천시'만 오는 경우가 있어, 둘을 병합해야
    # SGIS 인구·상권이 시·구 기준으로 정확히 조회된다.
    r_road = keywordgen.parse_region(biz.get("address") or "")
    r_jibun = keywordgen.parse_region(biz.get("address_jibun") or "")
    region = {
        "city": r_road["city"] or r_jibun["city"],
        "gu": r_road["gu"] or r_jibun["gu"],
        "dong": r_jibun["dong"] or r_road["dong"],
    }
    cls = keywordgen.classify(biz.get("category") or "")
    print("=" * 72)
    print(f"[업체 특정] {biz['title']}")
    print(f"  주소   : {biz['address']}")
    print(f"  업종   : {biz['category']}"
          + (f" → 진료과 {cls['dept']}" if cls["is_hospital"] and cls["dept"] else ""))
    print(f"  지역축 : 동={region['dong'] or '-'} / 구={region['gu'] or '-'} / 시={region['city'] or '-'}")
    if res["note"]:
        print(f"  참고   : {res['note']}")
    if len(res["candidates"]) > 1:
        print(f"  후보   : " + " | ".join(
            f"{c['title']}({(c.get('address_jibun') or c['address'] or '')[:14]}…)"
            for c in res["candidates"][:5]))

    # 2) 키워드 산출
    profile = dict(biz)
    kws = keywordgen.generate_keywords(profile, args.max_keywords)
    print(f"\n[키워드 자동 산출] {len(kws)}개")
    for k in kws:
        print(f"  - {k['kw']}  ({k['type']})")

    # 3) 키워드별 진단
    gu_hint = region["gu"] or region["city"]
    print(f"\n[진단 실행] 플레이스 + 통합검색 6영역 × {len(kws)}개 키워드 "
          f"(지역 검증: {gu_hint or '없음'})")
    rows = []
    for k in kws:
        pl = naver_local.keyword_exposure(k["kw"], biz["title"], region_hint=gu_hint)
        ct = naver_content.content_exposure(k["kw"], biz["title"])
        rows.append({**k, "place": pl, "content": ct})
        place_str = f"○{pl['rank']}" if pl["exposed"] else "✕"
        cells = " ".join(f"{lbl}:{cell(ct[key])}" for key, lbl in zip(SEC_KEYS, SEC_LABELS))
        amb = " ⚠검증필요" if pl.get("ambiguous") else ""
        print(f"  {k['kw']:<18} [{k['type']}] 플레이스:{place_str}{amb} | {cells}")

    # 4) 입지 분석
    location = None
    if not args.no_location:
        print("\n[입지 분석] SGIS 인구·상권" + (" + HIRA 경쟁" if cls["is_hospital"] else ""))
        location = analyze_location(biz, region, cls)
        if location:
            print(f"  행정구역   : {location.get('adm_nm') or '-'} (평균연령 {location.get('avg_age') or '-'})")
            print(f"  반경 1km 인구(추정): {location['population_radius']:,}명 · 수요 점수 {location['demand_score']}")
            print(f"  종사자 밀도 : {location['employee_density_km2']:,}/km² · 상권 활동 점수 {location['commerce_score']}")
            if "competitors" in location:
                print(f"  같은 진료과 경쟁: {location['competitors']}곳 · 경쟁 여유 {location['competition_score']}"
                      f" · 입지 참고 점수 {location['site_score']}")
        else:
            print("  지표 수집 실패 (SGIS 키/지역 확인)")

    # 4-b) 실제 위치 정적 지도 (NCP Maps, 키 있을 때만) — data URI로 임베드
    location_map = None
    if staticmap.available():
        location_map = staticmap.location_data_uri(
            biz.get("latitude"), biz.get("longitude"))
        print("\n[실제 지도] NCP Static Map "
              + ("임베드 완료" if location_map else "실패 — 좌표/키 확인"))

    # 5) 검색 수요 — 절대 월 검색량·연관검색어 (검색광고 키워드도구, 키 있을 때만)
    _norm = lambda s: "".join((s or "").split())
    search_demand = {"available": False, "volumes": {}, "related": []}
    if searchad.available():
        print("\n[검색 수요] 네이버 검색광고 키워드도구 — 월 검색량 · 연관검색어")
        kw_texts = [k["kw"] for k in kws if k["type"] != "브랜드"]
        vols = searchad.volumes_for(kw_texts) or {}
        volumes = {}
        for k in kws:
            row = vols.get(_norm(k["kw"]))
            if row:
                volumes[k["kw"]] = {"pc": row["pc"], "mobile": row["mobile"],
                                    "total": row["total"], "comp": row["comp"]}
        reg = region["gu"] or region["city"]
        seeds = [(f"{reg}{b}" if reg else b) for b in cls["base_terms"][:2] if b]
        rep = searchad.keyword_report(seeds or kw_texts[:2]) or []
        diag = {_norm(k["kw"]) for k in kws}
        related = [r for r in rep if _norm(r["kw"]) not in diag and r["total"] > 0][:15]
        search_demand = {"available": True, "volumes": volumes, "related": related}
        print(f"  검색량 확보 {len(volumes)}개 · 연관검색어 {len(related)}개")

    # 5-b) 실측 경쟁 벤치마크 — 상위 경쟁 병원을 같은 키워드로 조회한 영역별 노출률 평균.
    #      데이터 없으면 None → 리포트에서 '미측정'으로 표기(조작값 넣지 않음).
    benchmark = None
    benchmark_meta = None
    comp_list = (location or {}).get("competitor_list") or []
    if (comp_list and config.naver_available() and not args.no_benchmark
            and args.benchmark_competitors > 0):
        use = comp_list[:args.benchmark_competitors]
        print(f"\n[경쟁 벤치마크] 상위 경쟁 {len(use)}곳 실측 (키워드×6영역)")
        agg = {s: [] for s in SEC_KEYS}
        measured = 0
        for comp in use:
            per = {s: {"active": 0, "exposed": 0} for s in SEC_KEYS}
            got = False
            for r in rows:
                ce = naver_content.content_exposure(r["kw"], comp["name"])
                for s in SEC_KEYS:
                    cell = ce.get(s) or {}
                    if cell.get("present"):
                        per[s]["active"] += 1
                        got = True
                    if cell.get("exposed"):
                        per[s]["exposed"] += 1
            if got:
                measured += 1
                for s in SEC_KEYS:
                    a = per[s]["active"]
                    agg[s].append(round(100 * per[s]["exposed"] / a) if a else 0)
        if measured:
            benchmark = {s: round(sum(v) / len(v)) if v else 0 for s, v in agg.items()}
            benchmark_meta = {"n": measured, "basis": f"상위 경쟁 {measured}곳 평균(실측)"}
            print(f"  벤치마크 확보: {benchmark_meta['basis']}")
        else:
            print("  경쟁 벤치마크 미측정 (경쟁사 콘텐츠 데이터 없음)")

    # 5-c) 플레이스 품질 — 기본정보(전화·링크·분류)는 지역 API로 자동 실측,
    #      리뷰·평점·사진은 병원 직접 입력 시에만 반영(조작값 금지, 없으면 그 항목만 미측정).
    has_phone = bool(biz.get("telephone"))
    has_url = bool(biz.get("link"))
    has_cat = bool(biz.get("category"))
    info_completeness = round((has_phone + has_url + has_cat) / 3.0, 2)
    pq = scoring.place_quality_partial(
        review_count=args.reviews, rating=args.rating, photo_count=args.photos,
        info_completeness=info_completeness)
    place_info = {
        "signals": {"phone": has_phone, "url": has_url, "category": has_cat},
        "info_completeness": info_completeness,
        "reviews": args.reviews, "rating": args.rating, "photos": args.photos,
        "score": pq["score"], "frac": pq["frac"], "measured": pq["measured"],
    }
    print(f"\n[플레이스 품질] 기본정보 {int(info_completeness*100)}% 자동측정 "
          f"(전화 {'○' if has_phone else '✕'}·링크 {'○' if has_url else '✕'}·분류 {'○' if has_cat else '✕'})"
          + (f" · 리뷰/평점/사진 입력 반영" if (args.reviews or args.rating or args.photos) else " · 리뷰·평점·사진 미입력"))

    # 5-c-i) 플레이스 공개 집계값 자동 수집 — 내부·대면 전용(법무 검토 전·미검수).
    #        고객 리포트에는 넣지 않는다. 실패/불확실 시 값을 만들지 않고 '미측정'.
    place_internal = {"status": "미측정", "note": "자동 수집 미실행(--collect-place-metrics)"}
    if args.collect_place_metrics:
        m = naver_place.fetch_place_metrics(biz["title"], args.place_url or biz.get("link"))
        if m:
            place_internal = {"status": "수집(미검수)", **m}
            print(f"  [내부] 플레이스 집계 자동수집(미검수): 리뷰 {m.get('review')}·"
                  f"방문자 {m.get('visitor_review')}·블로그 {m.get('blog_review')}·"
                  f"사진 {m.get('photo')}·평점 {m.get('rating')} — 대면 전 실측 대조 필요")
        else:
            place_internal = {"status": "미측정", "note": "수집 실패/차단 — 값 생성 안 함(조작 금지)"}
            print("  [내부] 플레이스 집계 자동수집 실패 → 미측정")
    place_info["internal"] = place_internal  # visibility_scope=internal_admin_only

    # 5-d) 종합 마케팅 경쟁력 점수 — 측정된 축만(미측정 축 제외·재정규화, 조작값 금지)
    composite = scoring.composite_measured(
        exposure=scoring.exposure_score([r["place"] for r in rows]),
        density=(location or {}).get("competition_score"),
        demand=(location or {}).get("demand_score"),
        place=pq["score"], place_frac=pq["frac"],
    )

    # 6) 요약
    p_hit = sum(1 for r in rows if r["place"]["exposed"])
    c_hit = sum(1 for r in rows
                if any((r["content"].get(s) or {}).get("exposed") for s in SEC_KEYS))
    print("\n[요약]")
    print(f"  플레이스 노출        : {p_hit} / {len(rows)} 키워드")
    print(f"  콘텐츠 1영역 이상 노출: {c_hit} / {len(rows)} 키워드")
    ambiguous = any(r["place"].get("ambiguous") for r in rows)
    if ambiguous:
        print("  ⚠ 두 글자 이하 상호 — 동명 콘텐츠 오탐 가능, 노출 건 수동 검증 권장")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "business": biz, "region": region,
        "classification": cls, "keywords": rows, "location": location,
        "location_map": location_map,
        "benchmark": benchmark, "benchmark_meta": benchmark_meta,
        "composite": composite, "place": place_info,
        "actions": build_actions(rows, location, place_info, benchmark),
        "search_demand": search_demand,
        "resolution": {"note": res["note"],
                       "candidates": [{"title": c["title"], "address": c["address"]}
                                      for c in res["candidates"][:5]]},
        "summary": {"place_hit": p_hit, "content_hit": c_hit,
                    "total": len(rows), "ambiguous": ambiguous},
    }
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nJSON 저장: {p}")
    if args.html:
        p = Path(args.html)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_html(result), encoding="utf-8")
        print(f"HTML 저장: {p}")
    if args.html_masked:
        p = Path(args.html_masked)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_html(result, masked=True), encoding="utf-8")
        print(f"HTML(마킹판) 저장: {p}")


def render_html(j: dict, masked: bool = False) -> str:
    """자기완결 공유용 리포트 HTML (외부 의존성 0, CSP·noindex 포함).

    masked=True — 무료 배포용 마킹판. 노출 여부(○/✕/—)와 요약 개수는 공개하되
    세부 수치(위치 순번, 입지 수치)는 **HTML 생성 단계에서 제외**한다.
    (CSS 블러가 아니라 데이터 자체가 없으므로 소스 보기로도 확인 불가)
    문구는 법무 지침 준수 — 순위·보장 표현 없이 중립적으로 안내.
    """
    e = html_mod.escape
    b, r, cls, s = j["business"], j["region"], j["classification"], j["summary"]
    loc = j.get("location")

    LOCK = '<span class="lock" title="무료 회원 열람 시 공개">회원 공개</span>'

    # 검색 수요 (검색광고 키워드도구) — 키 연동 시에만 컬럼·섹션 노출
    sd = j.get("search_demand") or {}
    has_vol = bool(sd.get("available")) and not masked
    vols = sd.get("volumes") or {}

    def fmt_vol(n):
        if n is None:
            return "—"
        if n <= 5:
            return "10 미만"
        return f"{n:,}"

    def comp_txt(c):
        return f'<em> · {e(c)}</em>' if c else ""

    def vol_cell(kw):
        if not has_vol:
            return ""
        v = vols.get(kw)
        return f'<td class="c vol">{fmt_vol(v["total"]) if v else "—"}</td>'

    def cell_html(c):
        if not c or c.get("exposed") is None:
            return '<td class="c mut">미검증</td>'
        if not c.get("present"):
            return '<td class="c mut">—</td>'
        if c.get("exposed"):
            if masked:
                return f'<td class="c"><span class="hit">○</span> {LOCK}</td>'
            mix = 20 if c["position"] <= 5 else 12 if c["position"] <= 15 else 6
            return (f'<td class="c"><span class="hit" style="background:'
                    f'color-mix(in srgb, var(--good) {mix}%, transparent)">○ {c["position"]}</span></td>')
        return '<td class="c bad">✕</td>'

    def place_html(pl):
        if not pl.get("exposed"):
            return '<td class="c bad">✕</td>'
        amb = " ⚠" if pl.get("ambiguous") else ""
        if masked:
            return f'<td class="c good">○ {LOCK}</td>'
        return f'<td class="c good">○ {pl["rank"]}{amb}</td>'

    kw_rows = "".join(
        f'<tr><td><b>{e(k["kw"])}</b></td><td>{e(k["type"])}</td>'
        + vol_cell(k["kw"])
        + place_html(k["place"])
        + "".join(cell_html((k["content"] or {}).get(key)) for key in SEC_KEYS)
        + "</tr>"
        for k in j["keywords"])

    cands = " · ".join(e(c["title"]) for c in j["resolution"]["candidates"])

    def _band(sc):
        if sc >= 60:
            return "var(--good)", "우수"
        if sc >= 40:
            return "var(--warn)", "보통"
        return "var(--bad)", "낮음"

    def gauge_semi(score, label):
        """반원 속도계 게이지 — 낮음/보통/우수 구간 + 바늘로 기준 대비 위치를 직관화."""
        sc = max(0.0, min(float(score), 100.0))

        def pt(r, v):
            a = math.radians(180 - 1.8 * v)
            return (100 + r * math.cos(a), 100 - r * math.sin(a))

        def arc(r, v0, v1, color, w):
            x0, y0 = pt(r, v0)
            x1, y1 = pt(r, v1)
            return (f'<path d="M {x0:.1f} {y0:.1f} A {r} {r} 0 0 1 {x1:.1f} {y1:.1f}" '
                    f'fill="none" stroke="{color}" stroke-width="{w}"/>')

        nx, ny = pt(64, sc)
        col, lab = _band(sc)
        lab = "보통(평균권)" if lab == "보통" else lab
        zones = (arc(80, 0, 39, "color-mix(in srgb,var(--bad) 55%,var(--track))", 13)
                 + arc(80, 41, 59, "color-mix(in srgb,var(--warn) 62%,var(--track))", 13)
                 + arc(80, 61, 100, "color-mix(in srgb,var(--good) 55%,var(--track))", 13))
        return (f'<div class="gaugew"><svg viewBox="0 0 200 118" width="230" role="img" '
                f'aria-label="{label} {score}점">{zones}'
                f'<line x1="100" y1="100" x2="{nx:.1f}" y2="{ny:.1f}" stroke="var(--ink)" '
                f'stroke-width="3" stroke-linecap="round"/><circle cx="100" cy="100" r="5" fill="var(--ink)"/>'
                f'<text x="14" y="114" font-size="9" fill="var(--mut)">0</text>'
                f'<text x="100" y="18" font-size="9" fill="var(--mut)" text-anchor="middle">50·평균</text>'
                f'<text x="186" y="114" font-size="9" fill="var(--mut)" text-anchor="end">100</text></svg>'
                f'<div class="gv" style="color:{col}">{score}<span>/100</span></div>'
                f'<div class="gs" style="color:{col}">{lab}</div><div class="gl">{label}</div></div>')

    def zone_bar(label, score, meaning):
        """낮음/보통/우수 색 구간 위에 점수 마커 — 기준 대비 위치를 한눈에."""
        sc = max(0.0, min(float(score), 100.0))
        col, lab = _band(sc)
        return (f'<div class="zbar"><div class="zb-head"><span class="zl">{label}</span>'
                f'<span><b style="color:{col}">{score}</b> · <span style="color:{col};font-weight:700">{lab}</span></span></div>'
                f'<div class="zb-track">'
                f'<span class="zb-seg" style="flex:40;background:color-mix(in srgb,var(--bad) 20%,transparent)"></span>'
                f'<span class="zb-seg" style="flex:20;background:color-mix(in srgb,var(--warn) 26%,transparent)"></span>'
                f'<span class="zb-seg" style="flex:40;background:color-mix(in srgb,var(--good) 20%,transparent)"></span>'
                f'<span class="zb-mark" style="left:{sc:.1f}%"></span></div>'
                f'<div class="zb-scale"><span>낮음 0~40</span><span>보통</span><span>우수 60~100</span></div>'
                f'<div class="sm-mean">{meaning}</div></div>')

    def radius_map(n_comp):
        """반경 개략도 — 중심(우리)·1km/0.5km 원·경쟁 점(스키매틱, 실지도 아님)."""
        n = min(int(n_comp or 0), 45)
        dots = ""
        for i in range(n):
            ang = i * 2.399963  # 황금각
            rad = 76 * math.sqrt((i + 0.5) / max(n, 1))
            dx = 100 + rad * math.cos(ang)
            dy = 100 + rad * math.sin(ang)
            dots += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="2.6" fill="var(--bad)" opacity="0.65"/>'
        return (f'<svg viewBox="0 0 200 206" width="150" role="img" aria-label="반경 개략도">'
                f'<circle cx="100" cy="100" r="80" fill="color-mix(in srgb,var(--accent) 7%,transparent)" '
                f'stroke="var(--line)" stroke-width="1"/>'
                f'<circle cx="100" cy="100" r="40" fill="none" stroke="var(--line)" stroke-width="1" stroke-dasharray="3 3"/>'
                f'{dots}<circle cx="100" cy="100" r="6.5" fill="var(--accent)" stroke="var(--card)" stroke-width="2"/>'
                f'<text x="100" y="202" text-anchor="middle" font-size="9" fill="var(--mut)">● 우리 위치 · 원 = 0.5·1km · 붉은 점 = 경쟁</text></svg>')

    loc_html = ""
    if loc:
        meters_html = ""
        if masked:
            cards = [
                ("행정구역", e(loc.get("adm_nm") or "-"), "인구·연령 지표는 회원 공개"),
                ("반경 1km 인구(추정)", LOCK, "잠재 수요 점수 포함"),
                ("종사자 밀도·상권", LOCK, "상권 활동 점수 포함"),
            ]
            if "competitors" in loc:
                cards.append(("같은 진료과 경쟁 (1km)", LOCK, "경쟁 수·입지 참고 점수 포함"))
        else:
            basis = e(loc.get("density_basis") or "시·구 평균")
            # 행정구역: 긴 SGIS 명칭(강원특별자치도춘천시) 대신 지역축으로 짧게
            reg_main = e(r.get("gu") or r.get("city") or (loc.get("adm_nm") or "-"))
            reg_sub = (f'{e(r.get("city"))} · 평균연령 {loc.get("avg_age") or "-"}세'
                       if r.get("gu") and r.get("city") else f'평균연령 {loc.get("avg_age") or "-"}세')
            cards = [
                ("행정구역", reg_main, reg_sub),
                ("반경 1km 인구(추정)", f'{loc["population_radius"]:,}명', f'{basis} 밀도 기준'),
                ("종사자 밀도", f'{loc["employee_density_km2"]:,}/km²', '직장인 유동 근사'),
            ]
            if "competitors" in loc:
                cards.append(("같은 진료과 경쟁 (1km)", f'{loc["competitors"]}곳', '가까울수록 경쟁 치열'))
            hero = (f'<div class="gcol">{gauge_semi(loc["site_score"], "입지 참고 점수 · 종합")}</div>'
                    if "competitors" in loc else "")
            zbars = ""
            if "competitors" in loc:
                zbars += zone_bar("경쟁 여유", loc["competition_score"],
                                  "경쟁 밀도 대비 여유 — 높을수록 경쟁이 덜 치열")
            zbars += zone_bar("잠재 수요", loc["demand_score"], "반경 내 거주 인구 기반 수요 잠재력")
            zbars += zone_bar("상권 활동", loc["commerce_score"], "종사자 밀도 기반 상권 활동성")
            meters_html = (
                f'<h3 class="sm-title">점수 해설 '
                f'<span>기준 · <b style="color:var(--bad)">낮음</b> 0~40 · '
                f'<b style="color:var(--warn)">보통</b> 40~60(50=평균) · '
                f'<b style="color:var(--good)">우수</b> 60~100</span></h3>'
                f'<div class="scoregrid">{hero}<div class="zbars">{zbars}</div></div>')
        cs = "".join(f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div>'
                     f'<div class="d">{d}</div></div>' for k, v, d in cards)
        note = ("" if "competitors" in loc else
                "<p class='mut' style='font-size:12px;margin:10px 0 0'>경쟁 지표는 병원(HIRA 등록 기관) 전용입니다 — "
                "일반 업체는 인구·상권 지표만 제공합니다.</p>")
        nq = urllib.parse.quote((f'{b["title"]} {b.get("address") or ""}').strip())
        nmap = f'https://map.naver.com/p/search/{nq}'
        real_map = j.get("location_map") if not masked else None
        real_map_html = (
            f'<figure class="realmap"><img src="{real_map}" alt="{e(b["title"])} 실제 위치 지도" '
            f'width="680" height="320"><figcaption>실제 위치 · 네이버 지도 '
            f'<a href="{nmap}" target="_blank" rel="noopener">지도 앱에서 열기 →</a></figcaption></figure>'
            if real_map else "")
        schematic_cap = ("반경 1km 개략도 · "
                         + (("같은 진료과 " + str(loc["competitors"]) + "곳")
                            if "competitors" in loc else "인구·상권 기준"))
        # 실지도가 있으면 개략도는 '경쟁 밀도' 보조도로, 없으면 링크로 실위치 안내
        schematic_link = ("" if real_map else
                          f'<br><a href="{nmap}" target="_blank" rel="noopener" '
                          f'style="color:var(--accent);font-weight:700">네이버 지도에서 실제 위치 보기 →</a>')
        map_html = ("" if masked else
                    real_map_html
                    + f'<div class="mapwrap"><div class="mapbox">{radius_map(loc.get("competitors"))}</div>'
                    f'<div class="mapcap">{schematic_cap}{schematic_link}'
                    f'<br><span class="ds">위 개략도는 실제 지도가 아닌 위치·경쟁 밀도 표현입니다.'
                    f'{" 실제 지도는 상단 이미지 참고." if real_map else " 실제 지도는 위 링크로 확인하세요."}</span></div></div>')
        rb = loc.get("radius_breakdown") or {}
        radius_html = ""
        if rb and not masked:
            rfmt = lambda r: (f"{r/1000:g}km" if r >= 1000 else f"{r}m")
            cols = sorted(rb)
            hdr = "".join(f"<th>{rfmt(r)}</th>" for r in cols)
            rrow = lambda lab, fn: ("<tr><td>" + lab + "</td>"
                                    + "".join(f"<td>{fn(rb[r])}</td>" for r in cols) + "</tr>")
            radius_html = (
                '<h3 class="sm-title">반경별 비교</h3>'
                '<div class="tw"><table class="rtbl"><thead><tr><th>반경</th>' + hdr + '</tr></thead><tbody>'
                + rrow("경쟁 병원", lambda d: f'{d["competitors"]}곳')
                + rrow("경쟁 여유", lambda d: f'{d["competition_score"]:.0f}')
                + rrow("수요 점수", lambda d: f'{d["demand_score"]:.0f}')
                + '</tbody></table></div>'
                '<p class="ds">가까운 반경 = 실제 생활권 경쟁. 반경을 넓히면 경쟁·수요가 함께 커집니다(경쟁 여유·수요 점수는 당사 산식).</p>')

        loc_html = f'''
<section class="card"><h2>입지 참고 분석</h2>
  <div class="summary">{cs}</div>{note}
  {map_html}
  {radius_html}
  {meters_html}
  <p class="mut" style="font-size:12px;margin:14px 0 0">반경 인구·상권은 <b>{e(loc.get("density_basis") or "시·구 평균")} 밀도</b> 기준 추정입니다.
  {"넓은 시의 외곽(읍·면)이 도심 밀도를 희석하지 않도록, 사람이 실제 몰려 사는 곳의 밀도로 보정했습니다." if loc.get("density_basis","").startswith("체감") else ""}
  점수는 당사 산식 기준 상대 수준이며(50점 = 중간 기준선), 임대료·접근성·건물 조건 등 핵심 입지 변수는 포함하지 않는 참고 지표입니다(절대값보다 후보지 간 상대 비교용). 개원·영업 성과를 보장하지 않습니다.
  출처: SGIS 통계지리정보{" · 건강보험심사평가원 병원정보서비스" if "competitors" in loc else ""}.</p>
</section>'''

    amb_html = ('<p class="warn">⚠ 두 글자 이하 상호 특성상 동명 콘텐츠 오탐이 가능해 완전일치·제목 기준으로만 '
                '판정했으며, 노출 건은 수동 검증을 권장합니다.</p>' if s["ambiguous"] else "")

    mask_banner = ""
    if masked:
        locked_n = sum(1 for k in j["keywords"] if k["place"]["exposed"]) + sum(
            1 for k in j["keywords"] for key in SEC_KEYS
            if ((k["content"] or {}).get(key) or {}).get("exposed"))
        mask_banner = (f'<p class="warn" style="border-left:3px solid var(--accent)">'
                       f'이 리포트는 무료 배포판입니다. 세부 진단 항목 {locked_n}건(노출 위치·입지 수치)은 '
                       f'표시하지 않았으며, <b>무료 회원 열람 시 별도 비용 없이 전체가 공개</b>됩니다. '
                       f'노출 여부(○/✕) 판정과 요약은 그대로 확인하실 수 있습니다.</p>')

    # 영역별 상위 5 노출 현황 — '미노출'일 때 그 자리에 누가 떠 있는지 함께 보여준다.
    SEC_LABELS = [("blog", "블로그"), ("cafe", "카페"), ("web", "웹문서"),
                  ("news", "뉴스"), ("image", "이미지"), ("kin", "지식iN")]

    def _items_html(rows, me_pred):
        return "".join(
            f'<li class="{"me" if me_pred(it) else ""}"><span class="r">{it["rank"]}</span>'
            f'<span class="tt">{e((it.get("title") or "")[:70])}</span>'
            + (f'<span class="ad">{e((it.get("address") or "")[:22])}</span>' if it.get("address") else "")
            + "</li>"
            for it in rows)

    def top5_block(k):
        secs = []
        pl = k["place"] or {}
        if pl.get("results"):
            st = (f'<span class="good">○ 상위 {pl["rank"]}위</span>' if pl.get("exposed")
                  else '<span class="bad">미노출</span> · 이 자리 상위 5')
            body = _items_html(pl["results"][:5],
                               lambda it: pl.get("exposed") and it.get("rank") == pl.get("rank"))
            secs.append(("플레이스(지역)", st, body))
        for key, label in SEC_LABELS:
            c = (k["content"] or {}).get(key) or {}
            if not c.get("present") or not c.get("top"):
                continue
            st = (f'<span class="good">○ {c["position"]}위</span>' if c.get("exposed")
                  else '<span class="bad">미노출</span> · 이 자리 상위 5')
            body = _items_html(c["top"], lambda it: it.get("is_me"))
            secs.append((label, st, body))
        if not secs:
            return ""
        inner = "".join(
            f'<div class="sec"><div class="sec-h"><b>{lbl}</b>{st}</div>'
            f'<ol class="items">{body}</ol></div>' for lbl, st, body in secs)
        hit = k["place"].get("exposed") or any(
            ((k["content"] or {}).get(kk) or {}).get("exposed") for kk, _ in SEC_LABELS)
        badge = '' if hit else ' <span class="bad" style="font-size:11px">전 영역 미노출</span>'
        return f'<details class="top5"><summary>{e(k["kw"])}{badge}</summary>{inner}</details>'

    # ── 노출 한눈에 보기: 같은 데이터를 링 게이지·영역별 막대·단계별 막대로 다양화 ──
    def donut(hit, total, label, color):
        pct = round(100 * hit / total) if total else 0
        cc = 163.36  # 2πr, r=26
        frac = max(0, min(pct, 100)) / 100
        return (f'<div class="donut"><svg viewBox="0 0 72 72" width="76" height="76" role="img" '
                f'aria-label="{label} {pct}%"><circle cx="36" cy="36" r="26" fill="none" '
                f'stroke="var(--track)" stroke-width="8"/><circle cx="36" cy="36" r="26" fill="none" '
                f'stroke="{color}" stroke-width="8" '
                f'stroke-dasharray="{cc*frac:.1f} {cc:.1f}" transform="rotate(-90 36 36)"/>'
                f'<text x="36" y="41" text-anchor="middle" font-size="16" font-weight="800" '
                f'fill="var(--ink)">{pct}%</text></svg>'
                f'<div class="dl">{label}</div><div class="ds">{hit}/{total} 키워드</div></div>')

    def bar_row(label, over, under):
        # 막대는 해당 항목 자체를 100%로(노출 비율) — 우측 여백 없이 꽉 차게
        if not under:
            return (f'<div class="brow"><span class="bl">{label}</span>'
                    f'<span class="bt"></span>'
                    f'<span class="bv" style="color:var(--mut)">영역 없음</span></div>')
        fill = 100 * over / under
        col = "var(--good)" if over / under >= 0.5 else "var(--accent)" if over else "var(--mut)"
        return (f'<div class="brow"><span class="bl">{label}</span>'
                f'<span class="bt"><span class="over" style="width:{fill:.0f}%;background:{col}"></span></span>'
                f'<span class="bv">노출 <b>{over}</b>/{under}</span></div>')

    # 경쟁 벤치마크: 실측치(j["benchmark"])가 있을 때만 겹쳐 표시. 없으면 '미측정'.
    # 조작값을 절대 넣지 않는다(신뢰도 원칙).
    def coverage_charts():
        """영역별 커버리지 — 레이더(노출률 + 실측 경쟁 겹침) + 중첩 막대."""
        bench_src = j.get("benchmark")
        has_bench = isinstance(bench_src, dict) and any(v is not None for v in bench_src.values())
        bench_basis = (j.get("benchmark_meta") or {}).get("basis") or "상위 경쟁군 평균(실측)"
        secs = []
        for key, label in SEC_LABELS:
            active = sum(1 for k in j["keywords"] if ((k["content"] or {}).get(key) or {}).get("present"))
            exposed = sum(1 for k in j["keywords"] if ((k["content"] or {}).get(key) or {}).get("exposed"))
            secs.append({"label": label, "active": active, "exposed": exposed,
                         "pct": round(100 * exposed / active) if active else 0,
                         "bench": round(bench_src.get(key, 0)) if has_bench else None})
        n = len(secs)
        cx, cy, R = 150, 150, 92
        ang = lambda i: math.radians(-90 + 360 * i / n)
        px = lambda i, f: cx + R * f * math.cos(ang(i))
        py = lambda i, f: cy + R * f * math.sin(ang(i))
        ring = lambda g: " ".join(f"{px(i, g):.1f},{py(i, g):.1f}" for i in range(n))
        rings = "".join(f'<polygon points="{ring(g)}" fill="none" stroke="var(--line)" stroke-width="1"/>'
                        for g in (0.25, 0.5, 0.75, 1.0))
        axes = "".join(f'<line x1="{cx}" y1="{cy}" x2="{px(i,1):.1f}" y2="{py(i,1):.1f}" '
                       f'stroke="var(--line)" stroke-width="1"/>' for i in range(n))
        # 실측 경쟁 벤치마크 — 있을 때만 점선으로 겹침
        bench = ""
        if has_bench:
            bpoly = " ".join(f"{px(i, s['bench']/100):.1f},{py(i, s['bench']/100):.1f}" for i, s in enumerate(secs))
            bench = (f'<polygon points="{bpoly}" fill="var(--warn)" fill-opacity="0.08" '
                     f'stroke="var(--warn)" stroke-width="2" stroke-dasharray="5 4"/>')
        anyexp = any(s["pct"] for s in secs)
        if anyexp:
            dpoly = " ".join(f"{px(i, s['pct']/100):.1f},{py(i, s['pct']/100):.1f}" for i, s in enumerate(secs))
            data = (f'<polygon points="{dpoly}" fill="var(--accent)" fill-opacity="0.22" '
                    f'stroke="var(--accent)" stroke-width="2"/>')
        else:
            data = f'<circle cx="{cx}" cy="{cy}" r="3.5" fill="var(--accent)"/>'
        lx = lambda i: px(i, 1) + (18 if math.cos(ang(i)) > 0.1 else -18 if math.cos(ang(i)) < -0.1 else 0)
        labels = "".join(
            f'<text x="{lx(i):.1f}" y="{py(i,1)+(14 if math.sin(ang(i))>0.3 else -12 if math.sin(ang(i))<-0.3 else 2):.1f}" '
            f'text-anchor="middle" font-size="12" font-weight="700" fill="var(--ink)">{s["label"]}'
            f'<tspan x="{lx(i):.1f}" dy="14" font-size="11" fill="var(--accent)">{s["pct"]}%</tspan>'
            + (f'<tspan x="{lx(i):.1f}" dy="12.5" font-size="10" fill="var(--warn)">경쟁 {s["bench"]}%</tspan>'
               if has_bench else '')
            + '</text>'
            for i, s in enumerate(secs))
        legend = (f'<div class="cvlegend"><span><i class="lg lg-us"></i>우리 병원</span>'
                  f'<span><i class="lg lg-bm"></i>{e(bench_basis)}</span></div>'
                  if has_bench else
                  '<div class="cvlegend"><span><i class="lg lg-us"></i>우리 병원</span>'
                  '<span style="color:var(--mut)">경쟁 비교: <b>미측정</b>(경쟁사 데이터 수집 시 표시)</span></div>')
        radar = (f'<svg viewBox="0 0 300 340" width="100%" style="max-width:330px;display:block;margin:4px auto 0" '
                 f'role="img" aria-label="영역별 커버리지 레이더">{rings}{axes}{bench}{data}'
                 f'<circle cx="{cx}" cy="{cy}" r="2" fill="var(--mut)"/>{labels}</svg>' + legend)
        maxa = max((s["active"] for s in secs), default=0) or 1
        bars = "".join(
            f'<div class="cvrow"><span class="cvl">{s["label"]}</span>'
            f'<span class="cvbar"><span class="cvact" style="width:{100*s["active"]/maxa:.0f}%">'
            f'<span class="cvexp" style="width:{(100*s["exposed"]/s["active"]) if s["active"] else 0:.0f}%"></span>'
            f'</span></span>'
            f'<span class="cvv">노출 <b>{s["exposed"]}</b> / {s["active"]}</span></div>'
            for s in secs)
        radar_cap = ('영역별 · 활성 키워드 가운데 병원명 콘텐츠가 노출된 비율 · '
                     + ('<b style="color:var(--warn)">점선</b> = ' + e(bench_basis)
                        if has_bench else '경쟁 비교는 <b>미측정</b>'))
        return (f'<div class="cvgrid">'
                f'<div class="cvcard"><div class="ovh">통합검색 영역 커버리지 레이더</div>'
                f'<p class="ds">{radar_cap}</p>{radar}</div>'
                f'<div class="cvcard"><div class="ovh">영역별 노출 키워드 수</div>'
                f'<p class="ds">옅은 막대 = 영역이 활성인 키워드 · 진한 채움 = 그중 노출된 키워드</p>'
                f'<div class="cvbars">{bars}</div></div></div>')

    coverage_html = "" if masked else coverage_charts()

    # 종합 마케팅 경쟁력 점수 — 측정된 축만(미측정 축은 '미측정' 명시, 조작값 없음)
    composite_html = ""
    comp = j.get("composite")
    place_info = j.get("place") or {}
    if not masked and comp and comp.get("score") is not None:
        AX = [("exposure", "노출", "40%"), ("density", "경쟁 여유", "25%"),
              ("demand", "수요·입지", "20%"), ("place", "플레이스 품질", "15%")]
        rows_ax = ""
        for key, label, w in AX:
            v = (comp.get("components") or {}).get(key)
            if v is None:
                rows_ax += (f'<div class="cxrow"><span class="cxl">{label} <em>{w}</em></span>'
                            f'<span class="cxbar"></span><span class="cxv mut">미측정</span></div>')
            else:
                tag = ''
                if key == "place":
                    frac = comp.get("place_frac") or 1
                    if frac < 1:
                        tag = f' <em style="color:var(--mut)">부분 {int(frac*100)}%</em>'
                rows_ax += (f'<div class="cxrow"><span class="cxl">{label} <em>{w}</em>{tag}</span>'
                            f'<span class="cxbar"><span class="cxfill" style="width:{v:.0f}%"></span></span>'
                            f'<span class="cxv">{v:.0f}</span></div>')
        sig = place_info.get("signals") or {}
        yn = lambda b: '○' if b else '✕'
        rvp = " · ".join(
            (f'{lab} <b>{val}</b>' if val is not None else f'{lab} <span style="color:var(--mut)">미입력</span>')
            for lab, val in (("리뷰", place_info.get("reviews")), ("평점", place_info.get("rating")),
                             ("사진", place_info.get("photos"))))
        place_detail = (
            f'<p class="ds" style="margin-top:8px"><b>플레이스 기본정보</b>(지역 API 자동 측정): '
            f'전화 {yn(sig.get("phone"))} · 링크 {yn(sig.get("url"))} · 분류 {yn(sig.get("category"))} '
            f'— 완성도 {int((place_info.get("info_completeness") or 0)*100)}%. &nbsp;'
            f'<b>리뷰·평점·사진</b>: {rvp} — 병원이 자기 플레이스 값을 입력하면 실측 반영됩니다.</p>')
        mw = comp.get("measured_weight_pct")
        composite_html = (
            f'<section class="card"><h2>종합 마케팅 경쟁력 점수</h2>'
            f'<div class="cxgrid">{gauge_semi(round(comp["score"]), "종합 점수")}'
            f'<div class="cxbars">{rows_ax}</div></div>'
            f'{place_detail}'
            f'<p class="ds" style="margin-top:6px">노출 40·경쟁 25·수요 20·플레이스 15 가중(당사 산식). '
            f'미측정 항목은 조작값 없이 제외하고 측정된 축만으로 산정했습니다(반영 가중 {mw}%). '
            f'절대 순위·매출을 보장하지 않는 참고 지표입니다.</p></section>')

    overview_html = ""
    if not masked:
        kws = j["keywords"]
        total = len(kws) or 1
        place_hit = s["place_hit"]
        content_hit = s["content_hit"]
        overall = sum(1 for k in kws if k["place"]["exposed"]
                      or any(((k["content"] or {}).get(x) or {}).get("exposed") for x, _ in SEC_LABELS))
        donuts = (donut(overall, total, "종합 노출", "var(--accent)")
                  + donut(place_hit, total, "플레이스", "var(--teal)")
                  + donut(content_hit, total, "콘텐츠 영역", "var(--violet)"))
        # 영역별 커버리지 (활성 키워드 중 노출) — 막대는 영역별 노출 비율
        area_rows = ""
        for key, label in SEC_LABELS:
            act = sum(1 for k in kws if ((k["content"] or {}).get(key) or {}).get("present"))
            hit = sum(1 for k in kws if ((k["content"] or {}).get(key) or {}).get("exposed"))
            area_rows += bar_row(label, hit, act)
        # 지역 단계별 노출 (브랜드/동/구/시/메인) — 각 단계 자체 대비 노출 비율
        order = ["브랜드", "동단위", "구단위", "시단위", "메인"]
        lv = {}
        for k in kws:
            t = k["type"]
            ex = k["place"]["exposed"] or any(((k["content"] or {}).get(x) or {}).get("exposed") for x, _ in SEC_LABELS)
            d = lv.setdefault(t, [0, 0])
            d[1] += 1
            if ex:
                d[0] += 1
        level_rows = "".join(bar_row(t, lv[t][0], lv[t][1]) for t in order if t in lv)
        overview_html = f'''
<section class="card"><h2>노출 한눈에 보기</h2>
  <div class="donuts">{donuts}</div>
  <div class="leglow"><span style="color:var(--mut)">채운 만큼 = 해당 항목의 노출 비율 · 숫자 = 노출/대상 키워드</span></div>
  <div class="ovgrid" style="margin-top:12px">
    <div><p class="ovh">영역별 노출 커버리지</p>{area_rows}</div>
    <div><p class="ovh">지역 단계별 노출</p>{level_rows}
      <p class="ds" style="margin-top:8px">동·구·시로 좁힐수록 노출이 잡히는지 — 좁은 지역부터 채우는 게 유리</p></div>
  </div>
</section>'''

    # ── 원장님 관점: 진단 결과를 '궁금증 → 해답'으로 매핑 (전략 콘텐츠) ──
    guidance_html = ""
    if not masked:
        kws = j["keywords"]
        blog_ex = sum(1 for k in kws if ((k["content"] or {}).get("blog") or {}).get("exposed"))
        cafe_ex = sum(1 for k in kws if ((k["content"] or {}).get("cafe") or {}).get("exposed"))
        content_gap = (blog_ex + cafe_ex) <= 1
        place_ok = s["place_hit"] >= 1
        items = [
            ("유입 vs 전환",
             "플레이스엔 뜨는데 왜 신환이 안 늘까요?",
             ("노출(트래픽)과 내원(전환)은 다릅니다. " if place_ok else "")
             + "유입 뒤 보는 <b>콘텐츠 신뢰도·리뷰·예약 동선</b>에서 환자가 이탈합니다. "
               "노출 숫자보다 '들어와서 예약까지 이어지는 길'을 먼저 점검하세요."),
            ("검색 여정",
             "환자는 우리를 어디서 놓칠까요?",
             "환자는 ①증상 검색 → ②지역·치료법 비교 → ③병원명 검증 순으로 움직입니다. "
             "위 <b>지역 단계별 노출</b>로 ②를, <b>브랜드 노출</b>로 ③을 확인하고 빈 단계를 채우세요. "
             "(증상 키워드 ①은 별도 콘텐츠로 보강)"),
            ("콘텐츠 공백",
             "블로그·카페가 왜 중요한가요?",
             ("현재 블로그·카페 노출이 거의 없습니다. " if content_gap else "")
             + "환자가 <b>비교·검증</b>할 때 보는 자리입니다. 위 '상위 5'에서 그 자리를 누가 차지했는지 확인하고, "
               "광고성 글이 아니라 <b>원장님 진료 사례 기반 스토리텔링</b>으로 채워야 신뢰가 쌓입니다."),
            ("의료법 안전",
             "공격적으로 하고 싶은데 보건소가 무섭습니다",
             "지금 쓰는 블로그·소개 문구를 <b>리스크 진단</b>에 넣어 금지 표현(최고·완치·부작용 없음·할인 유인 등)을 "
             "먼저 걸러내세요. 안전 범위 안에서 하는 마케팅이 결국 오래갑니다."),
            ("리뷰 관리",
             "가짜 영수증 리뷰, 사도 되나요?",
             "돈 주고 산 리뷰는 네이버 필터링으로 삭제되고 <b>위반 대상</b>입니다. 실제 만족 환자가 자발적으로 남기게 하는 "
             "데스크 동선과, 악성 리뷰에 대응하는 <b>답글 운영</b>이 더 효과적입니다."),
            ("차별화(USP)",
             "우리 병원만의 강점을 어떻게 표현하죠?",
             "'친절·최신 장비'는 모두가 씁니다. 원장님의 <b>진료 철학·특정 시술 경험</b>처럼 우리만의 이야기를 뽑아 "
             "콘텐츠의 축으로 삼아야 환자에게 기억됩니다."),
        ]
        cards_g = "".join(
            f'<div class="gcard"><span class="gtag">{t}</span>'
            f'<p class="gq">{q}</p><p class="ga">{a}</p></div>' for t, q, a in items)
        # 우선 개선 액션 — 구조화 데이터(j["actions"])에서 렌더. compliance=safe → '의료광고 안전' 태그.
        acts = j.get("actions") or []
        acts_html = "".join(
            f'<div class="act"><div class="n">{a.get("priority", i+1)}</div><div>'
            f'<p class="t">{e(a.get("title", ""))}'
            + ('<span class="cok">의료광고 안전</span>' if a.get("compliance_status") == "safe"
               else '<span class="cwarn">검토 필요</span>' if a.get("compliance_status") else '')
            + f'</p><p class="p">{e(a.get("explanation", ""))}</p>'
            f'<p class="bs">근거: {e(a.get("evidence_metric", ""))}</p></div></div>'
            for i, a in enumerate(acts))
        guidance_html = (
            '<section class="card"><h2>컨설팅 소견 — 지금 무엇을 해야 하나</h2>'
            '<p class="mut" style="font-size:12px;margin:-4px 0 12px">이 진단 결과를 우선순위 액션과, 원장님이 실제로 '
            '궁금해하는 질문·"환자를 찾아오게 만드는" 해답으로 연결했습니다.</p>'
            f'<p class="subh">이번 달 우선 개선 액션</p><div class="acts">{acts_html}</div>'
            f'<p class="subh">원장님이 궁금해하는 것 → 해답</p><div class="guide">{cards_g}</div>'
            '<div class="gcta">더 구체적인 개선안이 필요하시면 — '
            '<a href="../self-check/#risk">의료법 리스크 자가진단</a> · '
            '<a href="../self-check/#journey">검색 여정 점검</a> · '
            '<a href="../landing/">무료 정밀검진 신청</a>. '
            '<span style="color:var(--sub)">모두 무료이며, 상담은 원하실 때만 드립니다(콜드콜 없음).</span></div>'
            '</section>')

    # ── 측정 기준 (항목/기준 표) — 대시보드 스타일 참고 ──
    _date = e(j["generated_at"][:10])
    basis_pairs = [
        ("노출 지표", f"네이버 오픈 API (비로그인, 지역 상위 5·콘텐츠 상위 30, {_date} 수집) — 실제 검색화면과 다를 수 있음"),
    ]
    if loc and "competitors" in loc:
        basis_pairs.append(("경쟁 병원", "건강보험심사평가원 병원정보서비스, 반경 1km 내 동일 진료과 표방 기준"))
    if loc:
        basis_pairs.append(("수요·상권", f"SGIS 통계지리정보 기반 잠재 수요 참고지표({e(loc.get('density_basis') or '시·구 평균')} 밀도 보정) — 실제 방문 수요를 보장하지 않음"))
    if has_vol:
        basis_pairs.append(("검색 수요", "네이버 검색광고 키워드도구 API — 월간 검색수(PC+모바일)·연관검색어·경쟁정도 · 광고주 계정 연동"))
    if j.get("location_map") and not masked:
        basis_pairs.append(("위치 지도", "네이버 클라우드 플랫폼 Static Map — 등록 주소 좌표 기준 실제 지도(생성 시점 임베드)"))
    basis_pairs += [
        ("콘텐츠 판정", "제목·요약 스니펫만 사용 · 본문 미수집·미저장"),
        ("진단 점수", "(주)베놈 산식에 따른 참고 지표 — 의료서비스의 질·치료 효과·환자 만족도·매출 가능성을 의미하지 않음"),
    ]
    basis_rows = "".join(f'<div class="btr"><div class="bk">{k}</div><div class="bd">{v}</div></div>'
                         for k, v in basis_pairs)
    basis_section = (
        '<section class="card"><h2>측정 기준</h2>'
        f'<div class="btbl">{basis_rows}</div>'
        '<p class="mut" style="font-size:11.5px;margin:12px 0 0"><b>고지</b> — 본 리포트는 병원 내부 운영 참고자료이며 '
        '의료광고물이 아닙니다. 환자 대상 광고·홍보물로 전재·캡처·인용·배포할 수 없습니다. 경쟁 병원에 대한 우열 판단·비방 목적으로 '
        '사용할 수 없습니다. 특정 검색 순위 달성·환자 유입·매출 증가를 보장하지 않습니다.</p></section>')

    top5_html = "".join(top5_block(k) for k in j["keywords"])
    top5_section = (f'''
<section class="card"><h2>영역별 상위 5 노출 현황</h2>
<p class="mut" style="font-size:12px;margin:-4px 0 12px">키워드를 펼치면 각 영역의 <b>상위 5건</b>을 보여줍니다.
미노출 영역도 "그 자리에 지금 누가 떠 있는지"를 함께 확인하세요. <b>파란 강조 = 우리 업체</b>.</p>
{top5_html}</section>''' if top5_html else "")

    # 검색 수요 · 연관 검색어 섹션 (검색광고 키워드도구 연동 시)
    vol_th = "<th>월 검색량</th>" if has_vol else ""
    demand_section = ""
    if has_vol:
        vlist = sorted(
            [dict(kw=kw, **v) for kw, v in vols.items() if v.get("total")],
            key=lambda x: x["total"], reverse=True)[:8]
        maxv = max((x["total"] for x in vlist), default=1) or 1
        vbars = "".join(
            f'<div class="vrow"><span class="vk">{e(x["kw"])}</span>'
            f'<span class="vbar"><span class="vfill" style="width:{max(4, round(100 * x["total"] / maxv))}%"></span></span>'
            f'<span class="vv">{fmt_vol(x["total"])}{comp_txt(x.get("comp"))}</span></div>'
            for x in vlist)
        related = sd.get("related") or []
        chips = "".join(
            f'<span class="chip">{e(x["kw"])}<b>{fmt_vol(x["total"])}</b></span>'
            for x in related)
        chips_block = (f'<h3 style="margin:18px 0 8px">연관 검색어 — 환자가 함께 쓰는 말</h3>'
                       f'<div class="chips">{chips}</div>') if chips else ""
        demand_section = f'''
<section class="card"><h2>검색 수요 · 연관 검색어</h2>
<p class="ds">네이버 검색광고 키워드도구 기준 <b>월간 검색수</b>(PC+모바일 합산). 실제 환자가 그 달에 이 말을 얼마나 검색했는지 —
노출을 어디부터 채울지 우선순위를 정하는 근거입니다.</p>
<div class="vwrap">{vbars}</div>
{chips_block}
<p class="ds" style="margin-top:12px">숫자 옆 <b>낮음·중간·높음</b> = 광고 경쟁 정도. <b>검색량은 많은데 위 매트릭스에서 미노출인 키워드가 1순위 공략 대상</b>입니다.</p>
</section>'''

    return f'''<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>자동 진단 — {e(b["title"])}</title>
<style>
:root{{--bg:#f6f8fb;--card:#fff;--ink:#1d2735;--sub:#5b6a7e;--line:#dfe6ef;--accent:#2a78d6;
--accent-soft:#e8f1fc;--accent-lite:#c3d9f4;--good:#1e8a4a;--bad:#c23a3a;--warn:#c98a00;--teal:#12897a;--violet:#6a54c0;
--track:#eef2f7;--mut:#8a97a8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#10161f;--card:#182130;--ink:#e8edf4;--sub:#9db0c5;
--line:#2a3648;--accent:#5b9ee8;--accent-soft:#1c2c42;--good:#4cc07a;--bad:#e07070;--warn:#d6a520;
--teal:#3fc0a8;--violet:#a48bec;--track:#222c3b;--mut:#71809a;--accent-lite:#27496f}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);line-height:1.6;
font-family:"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif}}
.wrap{{max-width:880px;margin:0 auto;padding:26px 16px 60px;display:flex;flex-direction:column;gap:18px}}
header{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:baseline;
border-bottom:2px solid var(--accent);padding-bottom:12px}}
.brand{{font-weight:800;color:var(--accent);white-space:nowrap}}
.bn{{font-size:11.5px;color:var(--sub)}}
h1{{font-size:24px;margin:8px 0 4px}}h2{{font-size:16.5px;margin:0 0 10px}}h3{{font-size:13.5px;margin:0}}
.meta{{font-size:13px;color:var(--sub)}}.meta b{{color:var(--ink)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.stat .k{{font-size:12px;color:var(--sub)}}.stat .v{{font-size:20px;font-weight:800;margin-top:2px}}
.stat .d{{font-size:11.5px;color:var(--sub)}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px;min-width:640px}}
th,td{{border-bottom:1px solid var(--line);padding:8px 9px;text-align:left}}
th{{font-size:11.5px;color:var(--sub);white-space:nowrap}}
td.c{{text-align:center;white-space:nowrap}}.good{{color:var(--good);font-weight:700}}
.bad{{color:var(--bad)}}.mut{{color:var(--mut)}}
.hit{{display:inline-block;min-width:44px;padding:1px 7px;border-radius:6px;color:var(--good);font-weight:700}}
.warn{{background:color-mix(in srgb, var(--bad) 8%, transparent);border:1px solid var(--line);
border-radius:8px;padding:9px 13px;font-size:12.5px;color:var(--sub)}}
.lock{{display:inline-block;font-size:10.5px;font-weight:700;color:var(--accent);
background:var(--accent-soft);border-radius:99px;padding:1px 8px;white-space:nowrap}}
.top5{{border:1px solid var(--line);border-radius:9px;margin-bottom:8px;background:var(--bg)}}
.top5>summary{{cursor:pointer;padding:10px 13px;font-weight:700;font-size:13.5px;list-style:none}}
.top5>summary::-webkit-details-marker{{display:none}}
.top5>summary::before{{content:"▸ ";color:var(--accent)}}
.top5[open]>summary::before{{content:"▾ "}}
.top5 .sec{{padding:2px 13px 10px}}
.top5 .sec-h{{display:flex;justify-content:space-between;align-items:baseline;font-size:12.5px;
border-bottom:1px solid var(--line);padding:6px 0 4px;margin-bottom:4px}}
.top5 .items{{margin:0;padding:0;list-style:none}}
.top5 .items li{{display:flex;gap:7px;align-items:baseline;font-size:12px;padding:2px 0;color:var(--sub)}}
.top5 .items li.me{{background:var(--accent-soft);color:var(--ink);font-weight:700;border-radius:5px;
padding:2px 6px;margin:1px -6px}}
.top5 .items .r{{flex:none;width:16px;color:var(--mut);font-variant-numeric:tabular-nums;text-align:right}}
.top5 .items .tt{{flex:1;overflow:hidden;text-overflow:ellipsis}}
.top5 .items .ad{{flex:none;color:var(--mut);font-size:11px}}
.sm-title{{font-size:13px;margin:16px 0 4px;display:flex;align-items:baseline;gap:8px}}
.rtbl{{min-width:auto;font-size:12.5px}}.rtbl th,.rtbl td{{text-align:center;padding:7px 10px;border-bottom:1px solid var(--line)}}
.rtbl thead th{{background:var(--track);font-weight:700}}.rtbl td:first-child,.rtbl th:first-child{{text-align:left;font-weight:700}}
.rtbl tbody td{{font-variant-numeric:tabular-nums}}
.sm-title span{{font-size:11px;color:var(--mut);font-weight:400}}
.smeters{{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px;margin-top:8px}}
@media (max-width:560px){{.smeters{{grid-template-columns:1fr}}}}
.smeter .sm-head{{display:flex;justify-content:space-between;align-items:baseline;font-size:12.5px;margin-bottom:5px}}
.smeter .sm-l{{font-weight:700}}
.smeter .sm-v{{font-variant-numeric:tabular-nums}}
.sm-track{{position:relative;height:9px;border-radius:5px;background:var(--track);overflow:visible}}
.sm-fill{{position:absolute;left:0;top:0;bottom:0;border-radius:5px}}
.sm-dot{{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;transform:translate(-50%,-50%);
border:2px solid var(--card);box-shadow:0 0 0 1px rgba(0,0,0,.15)}}
.sm-avg{{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--sub);opacity:.55}}
.sm-avg::after{{content:"평균";position:absolute;top:-13px;left:50%;transform:translateX(-50%);
font-size:9px;color:var(--sub);white-space:nowrap}}
.sm-scale{{display:flex;justify-content:space-between;font-size:9.5px;color:var(--mut);margin-top:4px}}
.sm-mean{{font-size:11px;color:var(--sub);margin-top:3px}}
.scoregrid{{display:flex;gap:22px;flex-wrap:wrap;align-items:center;margin-top:10px}}
.gcol{{flex:none;margin:0 auto}}
.gaugew{{text-align:center}}
.gaugew svg{{max-width:230px;width:100%;height:auto}}
.gaugew .gv{{font-size:27px;font-weight:800;line-height:1;margin-top:-8px}}
.gaugew .gv span{{font-size:13px;color:var(--sub);font-weight:600}}
.gaugew .gs{{font-size:13.5px;font-weight:800}}
.gaugew .gl{{font-size:12px;color:var(--sub);margin-top:1px}}
.zbars{{flex:1;min-width:250px;display:grid;gap:13px}}
.zbar .zb-head{{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:5px}}
.zbar .zb-head .zl{{font-weight:700}}
.zb-track{{position:relative;height:16px;border-radius:8px;overflow:hidden;display:flex}}
.zb-seg{{height:100%}}
.zb-mark{{position:absolute;top:-2px;bottom:-2px;width:3px;background:var(--ink);border-radius:2px;
transform:translateX(-50%);box-shadow:0 0 0 1.5px var(--card)}}
.zb-scale{{display:flex;justify-content:space-between;font-size:9.5px;color:var(--mut);margin-top:3px}}
.mapwrap{{display:flex;gap:18px;align-items:center;margin-top:14px;flex-wrap:wrap;justify-content:center}}
.mapbox{{flex:none}}.mapbox svg{{width:150px;height:auto}}
.mapcap{{font-size:12.5px;color:var(--sub);max-width:300px}}.mapcap .mut{{color:var(--mut);font-size:11px}}
.realmap{{margin:14px 0 0}}.realmap img{{width:100%;max-width:680px;height:auto;display:block;
margin:0 auto;border:1px solid var(--line);border-radius:12px}}
.realmap figcaption{{font-size:11.5px;color:var(--mut);text-align:center;margin-top:6px}}
.realmap figcaption a{{color:var(--accent);font-weight:700}}
.guide{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:6px}}
@media(max-width:600px){{.guide{{grid-template-columns:1fr}}}}
.gcard{{border:1px solid var(--line);border-radius:10px;padding:13px 15px;background:var(--bg)}}
.gcard .gtag{{display:inline-block;font-size:10.5px;font-weight:700;color:var(--accent);
background:var(--accent-soft);border-radius:5px;padding:1px 7px;margin-bottom:7px}}
.gcard .gq{{font-size:13px;font-weight:700;margin:0 0 5px}}.gcard .gq::before{{content:"Q. ";color:var(--accent)}}
.gcard .ga{{font-size:12.5px;color:var(--sub);margin:0;line-height:1.65}}.gcard .ga b{{color:var(--ink)}}
.gcta{{margin-top:14px;padding:13px 16px;border-radius:10px;background:var(--accent-soft);
font-size:13px;color:var(--ink)}}.gcta a{{color:var(--accent);font-weight:700}}
.acts{{display:flex;flex-direction:column;gap:10px;margin:2px 0 16px}}
.act{{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:12px 15px}}
.act .n{{flex:none;width:26px;height:26px;border-radius:7px;background:var(--accent);color:#fff;
display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px}}
.act .t{{font-size:13.5px;font-weight:700;margin:0 0 2px}}
.act .t .cok,.act .t .cwarn{{font-size:10px;font-weight:700;padding:1px 7px;border-radius:999px;margin-left:7px;vertical-align:1px}}
.act .t .cok{{background:color-mix(in srgb,var(--good) 15%,transparent);color:var(--good)}}
.act .t .cwarn{{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}}
.act .p{{font-size:12.5px;color:var(--sub);margin:0;line-height:1.6}}
.act .bs{{font-size:11.5px;color:var(--mut);margin-top:4px}}
.subh{{font-size:13px;font-weight:700;margin:4px 0 8px}}
.btbl{{border-top:2px solid var(--ink);margin-top:4px}}
.btr{{display:grid;grid-template-columns:120px 1fr;gap:14px;padding:9px 2px;border-bottom:1px solid var(--line);font-size:12.5px}}
.btr .bk{{font-weight:700}}.btr .bd{{color:var(--sub)}}
@media(max-width:520px){{.btr{{grid-template-columns:1fr;gap:2px}}}}
.donuts{{display:flex;gap:18px;flex-wrap:wrap;justify-content:center;margin:6px 0 4px}}
.donut{{text-align:center;flex:1;min-width:110px}}
.donut .dl{{font-size:12px;font-weight:700;margin-top:4px}}
.ds{{font-size:10.5px;color:var(--mut);line-height:1.5}}
td.vol{{font-variant-numeric:tabular-nums;font-weight:700;color:var(--ink)}}
.vwrap{{display:flex;flex-direction:column;gap:9px;margin:8px 0 2px}}
.vrow{{display:grid;grid-template-columns:150px 1fr 128px;align-items:center;gap:12px}}
.vk{{font-size:12.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.vbar{{height:12px;background:var(--track);border-radius:0;overflow:hidden}}
.vfill{{display:block;height:100%;background:var(--teal)}}
.vv{{font-size:12px;font-weight:800;text-align:right;font-variant-numeric:tabular-nums}}
.vv em{{font-style:normal;font-weight:600;color:var(--mut);font-size:11px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px}}
.chip{{display:inline-flex;align-items:center;gap:7px;font-size:12px;padding:6px 11px;
border:1px solid var(--line);border-radius:999px;background:var(--accent-soft)}}
.chip b{{font-variant-numeric:tabular-nums;color:var(--accent)}}
.cvgrid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:6px 0 18px}}
@media(max-width:640px){{.cvgrid{{grid-template-columns:1fr}}}}
.cvcard .ovh{{margin-bottom:2px}}
.cvbars{{display:flex;flex-direction:column;gap:11px;margin-top:10px}}
.cvrow{{display:grid;grid-template-columns:58px 1fr 96px;align-items:center;gap:10px}}
.cvl{{font-size:12.5px;font-weight:700}}
.cvbar{{height:13px;background:var(--track);overflow:hidden}}
.cvact{{position:relative;display:block;height:100%;background:var(--accent-lite)}}
.cvexp{{position:absolute;left:0;top:0;height:100%;background:var(--accent)}}
.cvv{{font-size:12px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums}}
.cvv b{{color:var(--accent)}}
.cvlegend{{display:flex;gap:18px;justify-content:center;font-size:11.5px;color:var(--sub);margin-top:2px}}
.cvlegend span{{display:inline-flex;align-items:center;gap:6px}}
.cvlegend .lg{{width:16px;height:0;border-top:2px solid;display:inline-block}}
.cvlegend .lg-us{{border-top-color:var(--accent)}}
.cvlegend .lg-bm{{border-top:2px dashed var(--warn)}}
.cxgrid{{display:grid;grid-template-columns:200px 1fr;gap:18px;align-items:center}}
@media(max-width:560px){{.cxgrid{{grid-template-columns:1fr}}}}
.cxbars{{display:flex;flex-direction:column;gap:9px}}
.cxrow{{display:grid;grid-template-columns:120px 1fr 44px;align-items:center;gap:10px}}
.cxl{{font-size:12.5px;font-weight:700}}.cxl em{{font-style:normal;color:var(--mut);font-weight:600;font-size:11px}}
.cxbar{{height:12px;background:var(--track);overflow:hidden}}
.cxfill{{display:block;height:100%;background:var(--accent)}}
.cxv{{font-size:12.5px;font-weight:800;text-align:right;font-variant-numeric:tabular-nums}}
.cxv.mut{{color:var(--mut);font-weight:700;font-size:11px}}
@media(max-width:520px){{.vrow{{grid-template-columns:110px 1fr 96px;gap:8px}}}}
.ovgrid{{display:grid;grid-template-columns:1fr 1fr;gap:16px 26px;margin-top:6px}}
@media (max-width:600px){{.ovgrid{{grid-template-columns:1fr}}}}
.ovh{{font-size:13px;font-weight:700;margin:0 0 8px}}
.brow{{display:flex;align-items:center;gap:9px;font-size:12px;margin:5px 0}}
.brow .bl{{width:52px;flex:none;color:var(--sub)}}
.brow .bt{{flex:1;height:13px;border-radius:3px;background:var(--track);position:relative;overflow:hidden}}
.brow .bt .over{{position:absolute;top:0;left:0;bottom:0;background:var(--accent)}}
.brow .bv{{width:72px;flex:none;text-align:right;color:var(--sub);font-variant-numeric:tabular-nums}}
.brow .bv b{{color:var(--ink)}}
.leglow{{font-size:11px;color:var(--mut);margin-top:8px;display:flex;gap:14px;flex-wrap:wrap}}
.leglow span{{display:inline-flex;align-items:center;gap:5px}}
.leglow i{{width:10px;height:10px;border-radius:3px;display:inline-block}}
footer{{font-size:11px;color:var(--mut);text-align:center;border-top:1px solid var(--line);padding-top:12px}}
@media print{{
  /* PDF/인쇄: 다크모드와 무관하게 항상 밝게, 카드 단위 페이지 나눔 최적화 */
  :root{{--bg:#fff;--card:#fff;--ink:#111;--sub:#333;--mut:#666;--line:#d3d9e0;--track:#eef0f3;
  --accent-soft:#eef4fc;--accent-lite:#cfe0f6}}
  body{{background:#fff;color:#111}}
  .wrap{{max-width:none;padding:0;gap:10px}}
  .card{{box-shadow:none;border-color:#d3d9e0}}
  .act,.gcard,.cvcard,.stat,.donut,.vrow,.cxrow,.brow{{break-inside:avoid}}
  h1,h2,h3,.ovh,.sm-title{{break-after:avoid}}
  thead{{display:table-header-group}}
  a{{color:#111;text-decoration:none}}
  .mask-banner{{border:1px solid #d3d9e0}}
  .print-hint{{display:none}}
  @page{{margin:14mm}}
}}
.print-hint{{font-size:11.5px;color:var(--mut);text-align:center;background:var(--accent-soft);
border-radius:8px;padding:8px 12px;margin:0}}
</style>
<div class="wrap">
<header><span class="brand">(주)베놈 VENOMAD</span>
<span class="bn">검색정보 운영 자동 진단 — 내부 참고용 · 전재 금지</span></header>
<section>
<h1>{e(b["title"])} 자동 진단 리포트</h1>
<div class="meta">입력: 업체명{"+지역 힌트" if j["resolution"]["candidates"] else ""} 하나 ·
업종 <b>{e(b.get("category") or "")}</b> · 주소 <b>{e(b.get("address") or "")}</b><br>
지역축 <b>{e(r.get("dong") or "-")}</b> / <b>{e(r.get("gu") or "-")}</b> / <b>{e(r.get("city") or "-")}</b>
· 수집 {e(j["generated_at"][:10])} · 네이버 오픈 API(비로그인)</div>
{"<div class='meta' style='margin-top:6px'>동명·유사 상호 후보: " + cands + "</div>" if len(j["resolution"]["candidates"]) > 1 else ""}
</section>
<section class="summary">
<div class="stat"><div class="k">자동 산출 키워드</div><div class="v">{s["total"]}개</div><div class="d">동/구/시/메인 × 업종·시술</div></div>
<div class="stat"><div class="k">플레이스 노출</div><div class="v">{s["place_hit"]} / {s["total"]}</div><div class="d">공식 API 상위 5건 기준</div></div>
<div class="stat"><div class="k">콘텐츠 노출 키워드</div><div class="v">{s["content_hit"]} / {s["total"]}</div><div class="d">6개 영역 중 1곳 이상</div></div>
</section>
{mask_banner}{amb_html}
{composite_html}
{overview_html}
<section class="card"><h2>키워드별 노출 매트릭스</h2>
{coverage_html}
<div class="tw"><table>
<thead><tr><th>키워드</th><th>유형</th>{vol_th}<th>플레이스</th><th>블로그</th><th>카페</th><th>웹문서</th><th>뉴스</th><th>이미지</th><th>지식iN</th></tr></thead>
<tbody>{kw_rows}</tbody></table></div>
<p class="mut" style="font-size:12px;margin:10px 0 0">○ N = 공식 API 결과 내 위치(실제 화면 순위 아님) ·
✕ = 상위 30건(플레이스 5건) 내 없음 · — = 이 키워드에는 해당 영역 없음.
파워링크(광고)·브랜드 콘텐츠·스마트블록 배치는 공식 API 미제공으로 측정 제외.</p>
</section>
{demand_section}
{top5_section}
{loc_html}
{guidance_html}
{basis_section}
<p class="print-hint">📄 PDF로 저장: 브라우저 인쇄(Ctrl/⌘+P) → 대상을 <b>'PDF로 저장'</b>으로 선택하세요.</p>
<footer>(주)베놈 VENOMAD · 자동 진단 산출물 — 내부 참고용, 광고물 전재 금지</footer>
</div>'''


if __name__ == "__main__":
    main()
