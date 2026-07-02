#!/usr/bin/env python3
"""1단계: 실데이터 월간 리포트 생성기.

세 데이터 소스(HIRA·네이버·SGIS)를 실키로 호출해 병원 1곳의 월간 검색 노출
경쟁력 리포트 HTML을 생성한다. 브라우저 인쇄로 PDF 변환 가능한 A4 레이아웃.

사용:
    python3 generate_report.py --name 라메스피부과의원 --lat 37.5079 --lng 127.0382
    python3 generate_report.py ... --out ../report/generated/report.html
"""

import argparse
import sys
from datetime import date
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import config, scoring                       # noqa: E402
from medirank.connectors import hira, naver_local, sgis    # noqa: E402
from medirank.geo import haversine_m                       # noqa: E402

DEFAULT_KEYWORDS = [
    "강남 피부과", "역삼역 피부과", "테헤란로 피부과", "강남 리프팅",
    "강남 보톡스", "강남 여드름치료", "강남 탈모치료", "강남 기미레이저",
]

GRADE_LABEL = {"top": ("top", "상위권"), "mid": ("mid", "중위권"),
               "none": ("none", "미노출")}


def collect(args) -> dict:
    """실데이터 수집 + 점수 계산."""
    hospitals = hira.fetch_hospitals_radius(args.lng, args.lat, args.radius,
                                            args.dgsbjt, rows=400)
    competitors = []
    for h in hospitals:
        if h["name"] == args.name or h.get("latitude") is None:
            continue
        d = haversine_m(args.lat, args.lng, h["latitude"], h["longitude"])
        competitors.append({"name": h["name"], "address": h["address"],
                            "distance_m": round(d, 1), "same_department": True})
    competitors.sort(key=lambda c: c["distance_m"])

    keyword_results = []
    for kw in args.keywords:
        r = naver_local.keyword_exposure(kw, args.name)
        grade = ("top" if r["exposed"] and r["rank"] and r["rank"] <= 3
                 else "mid" if r["exposed"] else "none")
        top1 = r["results"][0]["title"] if r["results"] else "-"
        keyword_results.append({**r, "grade": grade, "top1": top1})

    stats = sgis.area_stats(args.adm_cd)
    import math
    if stats and stats.get("ppltn_dnsty"):
        population = int(stats["ppltn_dnsty"] * math.pi * (args.radius / 1000) ** 2)
    else:
        stats, population = None, 42000

    exposure = scoring.exposure_score(keyword_results)
    density = scoring.density_score(competitors, args.radius)
    demand = scoring.demand_score(population, 0.45, args.radius)
    # 플레이스 집계값(리뷰/사진 수)은 법무 의견에 따라 자동 수집하지 않는다.
    # 병원이 직접 확인·입력한 값을 쓰며, 입력 전까지는 아래 기본값(예시)을 사용.
    place = scoring.place_quality_score(154, 4.4, 44, 0.7)
    final = scoring.final_score(exposure, density, demand, place)

    return {"competitors": competitors, "keywords": keyword_results,
            "stats": stats, "population": population,
            "scores": {"exposure": exposure, "density": density,
                       "demand": demand, "place": place, "final": final}}


def build_actions(data: dict) -> list[dict]:
    """의료광고 리스크가 없는 운영 개선 액션 3가지 (규칙 기반)."""
    actions = []
    weak = [k["keyword"] for k in data["keywords"] if k["grade"] == "none"]
    if weak:
        shown = "·".join(weak[:3])
        actions.append({
            "title": f"미노출 키워드 {len(weak)}개 커버리지 정비",
            "body": (f'"{shown}" 등이 공식 API 결과(상위 5위)에 없습니다. 해당 진료를 '
                     "실제 제공하는 경우에만 플레이스 소개·진료 항목·FAQ에 반영하세요."),
            "basis": f"근거: 키워드 {len(data['keywords'])}개 중 {len(weak)}개 미노출 · "
                     f"노출 점수 {data['scores']['exposure']}점"})
    if data["scores"]["density"] < 40:
        actions.append({
            "title": "생활권 세분 키워드로 경쟁 우회",
            "body": ("반경 내 경쟁 강도가 매우 높습니다. 광역 키워드 대신 역·동 단위 "
                     "지역 수식어(예: 역삼역, 언주역)와 세부 시술 조합 키워드의 "
                     "커버리지를 우선 확보하세요."),
            "basis": f"근거: 경쟁 밀도 점수 {data['scores']['density']}점 · "
                     f"반경 내 경쟁 {len(data['competitors'])}곳"})
    actions.append({
        "title": "네이버 플레이스 기본 정보 최신화",
        "body": ("대표 사진, 진료시간, 주차, 예약 링크를 실제 운영 상태와 맞추고 "
                 "진료 카테고리가 실제 진료 항목과 일치하는지 점검하세요."),
        "basis": "근거: 플레이스 품질 지표는 다음 단계에서 실측 수집 예정"})
    actions.append({
        "title": "리뷰 운영 프로세스 정비",
        "body": ("합법적인 만족도 조사와 재방문 안내 절차를 정비하세요. "
                 "대가성 리뷰 유도는 제안하지 않습니다."),
        "basis": "근거: 경쟁 병원 대비 리뷰 격차는 플레이스 실측 후 산출"})
    return actions[:3]


TEMPLATE = Template("""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>월간 검색 노출 경쟁력 리포트 · $hospital · $month_label</title>
<style>
  :root{--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
    --accent:#2a78d6;--accent-l:#b7d3f6;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--good-text:#006300}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif;
    color:var(--ink);line-height:1.5;background:#eee}
  .page{width:210mm;min-height:297mm;margin:10px auto;background:#fff;padding:16mm 15mm;position:relative}
  @media print{body{background:#fff}.page{margin:0;width:auto;min-height:auto;page-break-after:always}
    .no-print{display:none}@page{size:A4;margin:0}}
  .toolbar{max-width:210mm;margin:14px auto 0;display:flex;justify-content:flex-end}
  .toolbar button{font:inherit;font-size:13px;font-weight:600;padding:8px 16px;border-radius:8px;border:0;
    background:var(--accent);color:#fff;cursor:pointer}
  .rpt-head{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid var(--ink);
    padding-bottom:10px;margin-bottom:18px}
  .rpt-head .t1{font-size:12px;letter-spacing:.12em;color:var(--ink2);font-weight:700}
  .rpt-head h1{margin:2px 0 0;font-size:22px;letter-spacing:-.5px}
  .rpt-head .meta{text-align:right;font-size:12px;color:var(--ink2)}
  .hero{display:flex;gap:24px;align-items:center;border:1px solid var(--grid);border-radius:12px;
    padding:18px 22px;margin-bottom:16px}
  .hero .num{font-size:54px;font-weight:700;letter-spacing:-2px;line-height:1}
  .hero .num small{font-size:18px;color:var(--muted);font-weight:600;letter-spacing:0}
  .hero .desc{font-size:13px;color:var(--ink2);flex:1}
  h2{font-size:14.5px;margin:20px 0 8px;padding-left:9px;border-left:3px solid var(--accent)}
  .comp{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:4px}
  .comp .c{border:1px solid var(--grid);border-radius:10px;padding:10px 12px}
  .comp .c .l{font-size:11px;color:var(--ink2)} .comp .c .v{font-size:21px;font-weight:700}
  .comp .c .w{font-size:10.5px;color:var(--muted)}
  .comp .track{height:6px;border-radius:3px;background:var(--accent-l);margin-top:6px;overflow:hidden}
  .comp .fill{height:100%;background:var(--accent);border-radius:3px}
  table{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:6px}
  th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--grid)}
  th{font-size:10.5px;color:var(--ink2);border-bottom:1.5px solid var(--ink)}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
  .chip{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;font-weight:700;border-radius:99px;
    padding:1px 8px;border:1px solid var(--grid)}
  .chip .d{width:7px;height:7px;border-radius:50%}
  .chip.top .d{background:var(--good)} .chip.mid .d{background:var(--warn)} .chip.none .d{background:var(--crit)}
  .act{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--grid);border-radius:10px;
    padding:11px 14px;margin-bottom:8px}
  .act .n{flex:none;width:24px;height:24px;border-radius:7px;background:var(--accent);color:#fff;font-weight:800;
    font-size:13px;display:inline-flex;align-items:center;justify-content:center}
  .act h3{margin:0 0 2px;font-size:13px} .act p{margin:0;font-size:12px;color:var(--ink2)}
  .act .b{font-size:10.5px;color:var(--muted);margin-top:3px}
  .basis{margin-top:18px;border-top:1px solid var(--grid);padding-top:10px;font-size:10px;color:var(--muted)}
  .basis b{color:var(--ink2)}
</style></head><body>
<div class="toolbar no-print"><button onclick="window.print()">PDF로 저장 (인쇄)</button></div>
<div class="page">
  <div class="rpt-head">
    <div><div class="t1">(주)베놈 VENOMAD · 병원 검색정보 운영 진단 리포트 · 병원 내부 참고용 — 광고 전재 금지</div>
      <h1>$hospital — $month_label</h1></div>
    <div class="meta">분석 반경 $radius_label · 발행 $issued<br>실데이터 기반 · 리포트 ID $report_id</div>
  </div>
  <div class="hero">
    <div><div style="font-size:12px;color:var(--ink2)">검색정보 운영 진단 점수 (당사 산식)</div>
      <div class="num">$final_score<small> / 100</small></div></div>
    <div class="desc">$summary_text</div>
  </div>
  <h2>점수 구성</h2>
  <div class="comp">
    <div class="c"><div class="l">노출</div><div class="v">$s_exposure</div><div class="w">가중치 40%</div>
      <div class="track"><div class="fill" style="width:$s_exposure%"></div></div></div>
    <div class="c"><div class="l">경쟁 밀도</div><div class="v">$s_density</div><div class="w">가중치 25%</div>
      <div class="track"><div class="fill" style="width:$s_density%"></div></div></div>
    <div class="c"><div class="l">수요·입지</div><div class="v">$s_demand</div><div class="w">가중치 20%</div>
      <div class="track"><div class="fill" style="width:$s_demand%"></div></div></div>
    <div class="c"><div class="l">플레이스 품질</div><div class="v">$s_place</div><div class="w">가중치 15%</div>
      <div class="track"><div class="fill" style="width:$s_place%"></div></div></div>
  </div>
  <h2>키워드별 노출 진단 (네이버 공식 API 기준 · 상위 5위 이내)</h2>
  <table><thead><tr><th>키워드</th><th>API 기준</th><th class="num">API 내 위치</th><th>등급</th>
    <th>해당 키워드 1위 업체</th></tr></thead>
  <tbody>$keyword_rows</tbody></table>
  <h2>반경 내 인접 경쟁 병원 (건강보험심사평가원 기준)</h2>
  <table><thead><tr><th>병원</th><th class="num">거리</th><th>주소</th></tr></thead>
  <tbody>$competitor_rows</tbody></table>
  <h2>수요·입지 참고지표 (SGIS)</h2>
  $stats_block
  <h2>이번 달 우선 개선 액션</h2>
  $action_blocks
  <div class="basis"><b>측정 기준·고지</b> — 본 리포트는 병원 내부 운영 참고자료이며 의료광고물이 아닙니다.
  환자 대상 광고·홍보물로 전재, 캡처, 인용, 배포할 수 없습니다. 진단 점수와 등급은 (주)베놈 산식에 따른 참고
  지표이며, 특정 검색 순위 달성, 환자 유입, 매출 증가를 보장하지 않고 의료서비스의 질·치료 효과·환자 만족도를
  의미하지 않습니다. 경쟁 병원에 대한 우열 판단이나 비방 목적으로 사용할 수 없습니다.
  노출 지표: 네이버 지역 검색 API(비로그인 오픈 API, 결과 상위 5건, $issued 수집) — "미노출"은 API 응답 상위
  5건 밖을 의미하며 실제 검색화면과 다를 수 있습니다.
  경쟁 병원: 건강보험심사평가원 병원정보서비스(반경 $radius_label, 진료과목 표방 기준, 개원/폐업 반영 시차 가능).
  수요 지표: SGIS 잠재 수요 참고지표(실제 방문 수요 미보장). 개선 제안은 의료광고 규정을 준수하는
  운영·정보 정비 활동으로 한정됩니다. 문의·정정 요청: (주)베놈 venomad.</div>
</div></body></html>
""")


def render(args, data: dict) -> str:
    s = data["scores"]
    kw_rows = []
    for k in data["keywords"]:
        cls, label = GRADE_LABEL[k["grade"]]
        kw_rows.append(
            f"<tr><td><b>{k['keyword']}</b></td>"
            f"<td>{'노출' if k['exposed'] else '미노출'}</td>"
            f"<td class='num'>{k['rank'] if k['rank'] else '—'}</td>"
            f"<td><span class='chip {cls}'><span class='d'></span>{label}</span></td>"
            f"<td>{k['top1']}</td></tr>")
    comp_rows = [
        f"<tr><td>{c['name']}</td><td class='num'>{c['distance_m']:.0f}m</td>"
        f"<td>{(c['address'] or '')[:36]}</td></tr>"
        for c in data["competitors"][:8]]
    comp_rows.append(
        f"<tr><td colspan='3' style='color:var(--muted)'>가까운 8곳 표시 · "
        f"반경 {args.radius}m 전체 {len(data['competitors'])}곳</td></tr>")

    st = data["stats"]
    if st:
        stats_block = (
            f"<table><tbody>"
            f"<tr><td>행정구역</td><td class='num'>{st['adm_nm']} ({st['year']}년)</td></tr>"
            f"<tr><td>총인구 / 인구밀도</td><td class='num'>{st['tot_ppltn']:,.0f}명 · {st['ppltn_dnsty']:,.0f}명/km²</td></tr>"
            f"<tr><td>반경 {args.radius}m 환산 인구(밀도 기반 근사)</td><td class='num'>약 {data['population']:,}명</td></tr>"
            f"<tr><td>평균연령</td><td class='num'>{st['avg_age']}세</td></tr>"
            f"<tr><td>종사자 / 사업체 (상권 참고)</td><td class='num'>{st['employee_cnt']:,.0f}명 · {st['corp_cnt']:,.0f}곳</td></tr>"
            f"</tbody></table>")
    else:
        stats_block = "<p style='font-size:12px;color:var(--muted)'>SGIS 지표 조회 불가 — 기본값 사용</p>"

    action_blocks = "".join(
        f"<div class='act'><div class='n'>{i+1}</div><div><h3>{a['title']}</h3>"
        f"<p>{a['body']}</p><div class='b'>{a['basis']}</div></div></div>"
        for i, a in enumerate(build_actions(data)))

    exposed = sum(1 for k in data["keywords"] if k["exposed"])
    summary_text = (
        f"반경 {args.radius / 1000:g}km 안 같은 진료과 표방 의료기관 <b>{len(data['competitors'])}곳</b>과 "
        f"경쟁 중입니다. 핵심 키워드 {len(data['keywords'])}개 중 <b>{exposed}개</b>가 공식 API 상위 5위 안에 "
        f"노출되고 있으며, 경쟁 밀도가 높은 상권 특성상 세분 지역 키워드 공략과 커버리지 확대가 "
        f"이번 달 개선 포인트입니다.")

    month = args.month or date.today().strftime("%Y-%m")
    y, m = month.split("-")
    return TEMPLATE.substitute(
        hospital=args.name, month_label=f"{y}년 {int(m)}월",
        radius_label=f"{args.radius / 1000:g}km",
        issued=date.today().isoformat(),
        report_id=f"RPT-{month}-LIVE",
        final_score=f"{s['final']:.0f}",
        s_exposure=f"{s['exposure']:.0f}", s_density=f"{s['density']:.0f}",
        s_demand=f"{s['demand']:.0f}", s_place=f"{s['place']:.0f}",
        summary_text=summary_text,
        keyword_rows="".join(kw_rows), competitor_rows="".join(comp_rows),
        stats_block=stats_block, action_blocks=action_blocks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lng", type=float, required=True)
    ap.add_argument("--radius", type=int, default=1000, choices=[500, 1000, 1500, 2000])
    ap.add_argument("--dgsbjt", default="14")
    ap.add_argument("--adm-cd", default="11230")
    ap.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    ap.add_argument("--month", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not (config.hira_available() and config.naver_available()):
        print("경고: 실키가 없어 일부 데이터가 목업으로 대체됩니다.")
    data = collect(args)
    html = render(args, data)
    out = Path(args.out or f"{args.name}-{args.month or date.today().strftime('%Y-%m')}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"리포트 생성: {out} (최종 점수 {data['scores']['final']}점)")


if __name__ == "__main__":
    main()
