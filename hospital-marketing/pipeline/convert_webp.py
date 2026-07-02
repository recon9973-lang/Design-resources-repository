#!/usr/bin/env python3
"""독립 실행형 webP 일괄 변환 도구 — 자동글쓰기 등 다른 시스템과 무관하게 동작.

병원 사진·블로그 이미지 등 래스터 이미지(jpg/png)를 webP로 변환한다.
원본은 삭제하지 않으며, 변환 결과와 용량 절감을 표로 보고한다.

사용:
    pip install Pillow                          # 최초 1회
    python3 convert_webp.py <폴더 또는 파일> [--quality 82] [--out <출력폴더>]
    python3 convert_webp.py ./photos --quality 80

특징:
- jpg/jpeg/png/gif(1프레임) → webp. 이미 webp인 파일은 건너뜀
- EXIF 회전 반영, 알파 채널 보존, 메타데이터(위치정보 등) 제거(개인정보 보호)
- --max-width 지정 시 큰 이미지를 비율 유지 축소 (기본: 원본 크기 유지)
"""

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow가 필요합니다: pip install Pillow")

RASTER = {".jpg", ".jpeg", ".png", ".gif"}


def convert_one(src: Path, out_dir: Path | None, quality: int,
                max_width: int | None) -> tuple[Path, int, int] | None:
    dst = (out_dir or src.parent) / (src.stem + ".webp")
    if dst.exists():
        print(f"  건너뜀(이미 존재): {dst.name}")
        return None
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)  # EXIF 회전 반영
        if max_width and im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)),
                           Image.LANCZOS)
        # 메타데이터(EXIF·GPS) 미포함 저장 — 개인정보 보호
        params = {"quality": quality, "method": 6}
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            params["lossless"] = False
        else:
            im = im.convert("RGB")
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "WEBP", **params)
    return dst, src.stat().st_size, dst.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="변환할 폴더 또는 이미지 파일")
    ap.add_argument("--quality", type=int, default=82, help="webP 품질 1-100 (기본 82)")
    ap.add_argument("--max-width", type=int, default=None, help="최대 가로 픽셀 (초과 시 축소)")
    ap.add_argument("--out", default=None, help="출력 폴더 (기본: 원본 옆)")
    args = ap.parse_args()

    target = Path(args.target)
    out_dir = Path(args.out) if args.out else None
    files = ([target] if target.is_file()
             else sorted(p for p in target.rglob("*") if p.suffix.lower() in RASTER))
    if not files:
        print(f"변환 대상 래스터 이미지가 없습니다: {target}")
        return

    total_before = total_after = 0
    print(f"{len(files)}개 파일 변환 (품질 {args.quality}) ...")
    for f in files:
        try:
            r = convert_one(f, out_dir, args.quality, args.max_width)
        except Exception as e:  # 손상 파일 등은 건너뛰고 계속
            print(f"  실패: {f.name} — {e}")
            continue
        if r:
            dst, before, after = r
            total_before += before
            total_after += after
            print(f"  {f.name} → {dst.name}  {before/1024:,.0f}KB → {after/1024:,.0f}KB "
                  f"({(1 - after / before) * 100:.0f}% 절감)")
    if total_before:
        print(f"\n합계: {total_before/1024:,.0f}KB → {total_after/1024:,.0f}KB "
              f"({(1 - total_after / total_before) * 100:.0f}% 절감)")


if __name__ == "__main__":
    main()
