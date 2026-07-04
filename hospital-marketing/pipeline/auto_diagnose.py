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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import keywordgen, scoring                          # noqa: E402
from medirank.connectors import hira, naver_content, naver_local, sgis  # noqa: E402
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

    dnsty = (stats or {}).get("ppltn_dnsty") or 13000.0
    pop_radius = int(dnsty * math.pi * (radius_m / 1000.0) ** 2)
    demand = scoring.demand_score(pop_radius, 0.45, radius_m)
    out.update({"population_radius": pop_radius, "demand_score": demand})

    emp_density = 0.0
    if stats and stats.get("employee_cnt") and stats.get("tot_ppltn") and dnsty:
        gu_area = stats["tot_ppltn"] / dnsty
        emp_density = stats["employee_cnt"] / gu_area if gu_area else 0.0
    commerce = round(100.0 * (1.0 - math.exp(-emp_density / 25000.0)), 1)
    out.update({"employee_density_km2": round(emp_density), "commerce_score": commerce})

    dgsbjt = keywordgen.DEPT_DGSBJT.get(cls.get("dept") or "")
    if cls.get("is_hospital") and dgsbjt and lat is not None and lng is not None:
        hospitals = hira.fetch_hospitals_radius(lng, lat, radius_m, dgsbjt, rows=1000)
        comps = [{"distance_m": haversine_m(lat, lng, h["latitude"], h["longitude"]),
                  "same_department": True}
                 for h in hospitals if h.get("latitude") is not None]
        comps = [c for c in comps if c["distance_m"] <= radius_m]
        density = scoring.density_score(comps, radius_m)
        site = round(0.5 * density + 0.3 * demand + 0.2 * commerce, 1)
        out.update({
            "competitors": len(comps), "competition_score": density,
            "saturation_per_10k": round(len(comps) / (pop_radius / 10000.0), 1) if pop_radius else None,
            "site_score": site,
        })
    return out


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

    # 5) 요약
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
        + place_html(k["place"])
        + "".join(cell_html((k["content"] or {}).get(key)) for key in SEC_KEYS)
        + "</tr>"
        for k in j["keywords"])

    cands = " · ".join(e(c["title"]) for c in j["resolution"]["candidates"])

    loc_html = ""
    if loc:
        if masked:
            cards = [
                ("행정구역", e(loc.get("adm_nm") or "-"), "인구·연령 지표는 회원 공개"),
                ("반경 1km 인구(추정)", LOCK, "잠재 수요 점수 포함"),
                ("종사자 밀도·상권", LOCK, "상권 활동 점수 포함"),
            ]
            if "competitors" in loc:
                cards.append(("같은 진료과 경쟁 (1km)", LOCK, "경쟁 수·입지 참고 점수 포함"))
        else:
            cards = [
                ("행정구역", e(loc.get("adm_nm") or "-"), f'평균연령 {loc.get("avg_age") or "-"}세'),
                ("반경 1km 인구(추정)", f'{loc["population_radius"]:,}명', f'잠재 수요 점수 {loc["demand_score"]}'),
                ("종사자 밀도", f'{loc["employee_density_km2"]:,}/km²', f'상권 활동 점수 {loc["commerce_score"]}'),
            ]
            if "competitors" in loc:
                cards.append(("같은 진료과 경쟁 (1km)", f'{loc["competitors"]}곳',
                              f'경쟁 여유 {loc["competition_score"]} · 입지 참고 {loc["site_score"]}점'))
        cs = "".join(f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div>'
                     f'<div class="d">{d}</div></div>' for k, v, d in cards)
        note = ("" if "competitors" in loc else
                "<p class='mut' style='font-size:12px;margin:10px 0 0'>경쟁 지표는 병원(HIRA 등록 기관) 전용입니다 — "
                "일반 업체는 인구·상권 지표만 제공합니다.</p>")
        loc_html = f'''
<section class="card"><h2>입지 참고 분석</h2>
  <div class="summary">{cs}</div>{note}
  <p class="mut" style="font-size:12px;margin:10px 0 0"><b>반경 인구·상권 지표는 시·구 평균 밀도 기준 추정</b>이라,
  도심·번화가(예: 춘천 중앙로처럼 넓은 시의 중심가)는 실제보다 낮게 나올 수 있습니다 — 절대값이 아니라 후보지 간 상대 비교용으로 보세요.
  임대료·접근성·건물 조건 등 핵심 입지 변수를 포함하지 않는 참고 지표이며, 개원·영업 성과를 보장하지 않습니다.
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

    top5_html = "".join(top5_block(k) for k in j["keywords"])
    top5_section = (f'''
<section class="card"><h2>영역별 상위 5 노출 현황</h2>
<p class="mut" style="font-size:12px;margin:-4px 0 12px">키워드를 펼치면 각 영역의 <b>상위 5건</b>을 보여줍니다.
미노출 영역도 "그 자리에 지금 누가 떠 있는지"를 함께 확인하세요. <b>파란 강조 = 우리 업체</b>.</p>
{top5_html}</section>''' if top5_html else "")

    return f'''<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>자동 진단 — {e(b["title"])}</title>
<style>
:root{{--bg:#f6f8fb;--card:#fff;--ink:#1d2735;--sub:#5b6a7e;--line:#dfe6ef;--accent:#2a78d6;
--accent-soft:#e8f1fc;--good:#1e8a4a;--bad:#c23a3a;--mut:#8a97a8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#10161f;--card:#182130;--ink:#e8edf4;--sub:#9db0c5;
--line:#2a3648;--accent:#5b9ee8;--accent-soft:#1c2c42;--good:#4cc07a;--bad:#e07070;--mut:#71809a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);line-height:1.6;
font-family:"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif}}
.wrap{{max-width:880px;margin:0 auto;padding:26px 16px 60px;display:flex;flex-direction:column;gap:18px}}
header{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:baseline;
border-bottom:2px solid var(--accent);padding-bottom:12px}}
.brand{{font-weight:800;color:var(--accent);white-space:nowrap}}
.bn{{font-size:11.5px;color:var(--sub)}}
h1{{font-size:22px;margin:8px 0 4px}}h2{{font-size:15.5px;margin:0 0 10px}}
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
footer{{font-size:11px;color:var(--mut);text-align:center;border-top:1px solid var(--line);padding-top:12px}}
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
<section class="card"><h2>키워드별 노출 매트릭스</h2>
<div class="tw"><table>
<thead><tr><th>키워드</th><th>유형</th><th>플레이스</th><th>블로그</th><th>카페</th><th>웹문서</th><th>뉴스</th><th>이미지</th><th>지식iN</th></tr></thead>
<tbody>{kw_rows}</tbody></table></div>
<p class="mut" style="font-size:12px;margin:10px 0 0">○ N = 공식 API 결과 내 위치(실제 화면 순위 아님) ·
✕ = 상위 30건(플레이스 5건) 내 없음 · — = 이 키워드에는 해당 영역 없음.
파워링크(광고)·브랜드 콘텐츠·스마트블록 배치는 공식 API 미제공으로 측정 제외.</p>
</section>
{top5_section}
{loc_html}
<section class="card"><h2>데이터 기준 고지</h2>
<p class="mut" style="font-size:12.5px;margin:0">본 자료는 (주)베놈이 당사 산식으로 산출한 검색정보 운영 진단
결과이며, 특정 순위 달성·상위 노출·매출 증가를 보장하지 않습니다. 출처: 네이버 오픈 API(지역·블로그·카페·웹문서·뉴스·이미지·지식iN)
· SGIS 통계지리정보{" · 건강보험심사평가원" if loc and "competitors" in loc else ""}. 제목·요약 스니펫만 판정에 사용하며 본문은 수집·저장하지 않습니다.</p>
</section>
<footer>(주)베놈 VENOMAD · 자동 진단 산출물 — 내부 참고용, 광고물 전재 금지</footer>
</div>'''


if __name__ == "__main__":
    main()
