#!/usr/bin/env python3
"""병원 1곳의 월간 마케팅 경쟁력 지표를 계산해 JSON으로 출력한다.

사용:
    python3 run_report.py                       # 목업 데이터로 샘플 병원 분석
    python3 run_report.py --radius 500          # 반경 변경
    python3 run_report.py --out metrics.json    # 파일 저장

API 키(.env 또는 환경변수)가 있으면 실데이터, 없으면 fixtures 목업으로 동작.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medirank.report import build_monthly_metrics  # noqa: E402

SAMPLE_HOSPITAL = {
    "name": "밝은빛피부과의원",
    "latitude": 37.5006,
    "longitude": 127.0364,
    "department_name": "피부과",
}

SAMPLE_KEYWORDS = [
    "테헤란로 피부과", "역삼역 피부과", "강남 보톡스", "강남 피부과",
    "강남 리프팅", "강남 여드름치료", "강남 피부관리", "강남 탈모치료",
    "강남 기미레이저", "강남 흉터치료",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radius", type=int, default=1000,
                    choices=[500, 1000, 1500, 2000])
    ap.add_argument("--month", default=None, help="YYYY-MM (기본: 이번 달)")
    ap.add_argument("--out", default=None, help="JSON 저장 경로")
    args = ap.parse_args()

    metrics = build_monthly_metrics(
        my_hospital=SAMPLE_HOSPITAL,
        keywords=SAMPLE_KEYWORDS,
        radius_m=args.radius,
        month=args.month,
    )
    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"저장: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
