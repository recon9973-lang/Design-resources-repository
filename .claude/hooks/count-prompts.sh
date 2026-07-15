#!/usr/bin/env bash
# UserPromptSubmit 훅 — 이 세션의 질문/오더 수를 세고, 20건마다 체크포인트를 안내한다.
# stdout은 컨텍스트로 주입되므로, 임계 도달 시에만 지시를 출력한다(평상시 무출력).
set -eu

input="$(cat 2>/dev/null || true)"
# session_id 추출(견고하게, 실패 시 컨테이너 공용 카운터 사용)
sid="$(printf '%s' "$input" | grep -oE '"session_id"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')"
[ -z "${sid:-}" ] && sid="container"

f="/tmp/claude-checkpoint-${sid}.count"
n="$(cat "$f" 2>/dev/null || echo 0)"
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))
echo "$n" > "$f"

# 20건마다: 무거운 핸드오프(문서화 + /clear)
if [ "$((n % 20))" -eq 0 ]; then
  cat <<EOF
🔔 [세션 자동 체크포인트 · ${n}건] 질문/오더가 20건 배수에 도달했습니다.
지금 사용자 답변을 마친 뒤 **checkpoint 스킬**을 실행하세요:
① 세션 작업 20건을 docs/session-logs/ 에 문서화 → ② TODO 갱신 →
③ RESUME.md(다음 세션 이어갈 프롬프트) 생성 → ④ PROJECT_STATE 갱신 →
⑤ desktop-tutorial main에 커밋·푸시 → ⑥ 사용자에게 "/clear 입력"을 명시적으로 안내.
(토큰 누적 완화를 위한 자동 인계 시스템)
EOF
# 그 사이 10건 지점(20의 절반): 가벼운 /compact 권장 — 연속성 유지·컨텍스트만 압축
elif [ "$((n % 20))" -eq 10 ]; then
  cat <<EOF
💡 [컨텍스트 압축 권장 · ${n}건] 세션이 절반쯤 쌓였습니다. 아직 인계할 정도는 아니지만,
답변을 마친 뒤 사용자에게 **"/compact"** 실행을 가볍게 권하세요(대화 맥락은 유지한 채
컨텍스트만 요약·압축해 토큰을 아낌). 20건 도달 시엔 checkpoint→/clear로 전환합니다.
EOF
fi
exit 0
