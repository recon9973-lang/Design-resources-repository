#!/usr/bin/env bash
# SessionStart 훅 — RESUME.md가 있으면 그 내용을 세션 시작 컨텍스트로 주입한다.
# 다음 세션이 "저장한 프롬프트 불러오기"를 자동으로 수행하는 장치.
set -eu
dir="${CLAUDE_PROJECT_DIR:-.}"
r="$dir/RESUME.md"
if [ -f "$r" ]; then
  echo "===== 이어갈 작업: RESUME.md (직전 세션 핸드오프) ====="
  cat "$r"
  echo "===== RESUME 끝 · 위 '바로 이어갈 작업'부터 재개하세요 ====="
fi
exit 0
