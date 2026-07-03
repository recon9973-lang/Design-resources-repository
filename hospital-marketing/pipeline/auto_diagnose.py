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
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank import keywordgen                                  # noqa: E402
from medirank.connectors import naver_content, naver_local       # noqa: E402

SEC_KEYS = ["blog", "cafe", "web", "news", "image", "kin"]
SEC_LABELS = ["블로그", "카페", "웹문서", "뉴스", "이미지", "지식iN"]


def cell(c: dict) -> str:
    if not c or c.get("exposed") is None:
        return "미검증"
    if not c.get("present"):
        return "—"
    return f"○{c['position']}" if c.get("exposed") else "✕"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="업체명 (이것만 있으면 됨)")
    ap.add_argument("--region", default=None, help="동명 업체 구분용 지역 힌트 (예: 대구, 수성구)")
    ap.add_argument("--max-keywords", type=int, default=12)
    ap.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = ap.parse_args()

    # 1) 업체 특정
    res = keywordgen.resolve_business(args.name, args.region)
    if not res["chosen"]:
        print(f"업체를 찾지 못했습니다: {args.name} ({res['note']})")
        sys.exit(1)
    biz = res["chosen"]
    region = keywordgen.parse_region(biz.get("address_jibun") or biz["address"] or "")
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

    # 4) 요약
    p_hit = sum(1 for r in rows if r["place"]["exposed"])
    c_hit = sum(1 for r in rows
                if any((r["content"].get(s) or {}).get("exposed") for s in SEC_KEYS))
    print("\n[요약]")
    print(f"  플레이스 노출        : {p_hit} / {len(rows)} 키워드")
    print(f"  콘텐츠 1영역 이상 노출: {c_hit} / {len(rows)} 키워드")
    ambiguous = any(r["place"].get("ambiguous") for r in rows)
    if ambiguous:
        print("  ⚠ 두 글자 이하 상호 — 동명 콘텐츠 오탐 가능, 노출 건 수동 검증 권장")

    if args.out:
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "business": biz, "region": region,
            "classification": cls, "keywords": rows,
            "summary": {"place_hit": p_hit, "content_hit": c_hit,
                        "total": len(rows), "ambiguous": ambiguous},
        }
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n저장: {p}")


if __name__ == "__main__":
    main()
