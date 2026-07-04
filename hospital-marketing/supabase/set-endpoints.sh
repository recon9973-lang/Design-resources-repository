#!/usr/bin/env bash
# 프론트 4개 화면의 엔드포인트 상수를 배포된 프로젝트 값으로 일괄 주입.
# 사용:  bash supabase/set-endpoints.sh <PROJECT_REF> <ANON_KEY>
#   PROJECT_REF: xxxx  (프로젝트 URL https://xxxx.supabase.co 의 xxxx)
#   ANON_KEY   : Supabase 대시보드 → Project Settings → API → anon public key
set -euo pipefail
REF="${1:?사용법: set-endpoints.sh <PROJECT_REF> <ANON_KEY>}"
ANON="${2:?ANON_KEY 가 필요합니다}"
cd "$(dirname "$0")/.."   # → hospital-marketing/
BASE="https://${REF}.supabase.co"
FN="${BASE}/functions/v1"

# const NAME = '...'  (공백 유무 무관) 의 값만 치환
set_const () {
  perl -0pi -e "s|(const\s+\Q$2\E\s*=\s*)'[^']*'|\${1}'$3'|g" "$1"
  grep -q "$3" "$1" && echo "  ✓ $1 : $2" || echo "  ⚠ $1 : $2 (매칭 실패 — 수동 확인)"
}
echo "엔드포인트 주입 (base: $BASE)"
set_const landing/index.html      LEADS_ENDPOINT      "$FN/leads"
set_const report-view/index.html  SUPABASE_URL        "$BASE"
set_const report-view/index.html  SUPABASE_ANON       "$ANON"
set_const report-view/index.html  REPORTS_ENDPOINT    "$FN/reports"
set_const self-check/index.html   SELF_CHECK_ENDPOINT "$FN/self-check"
set_const admin/index.html        ADMIN_ENDPOINT      "$FN/admin-leads"
echo "✅ 완료. 'git diff' 로 확인 후 커밋·Pages 배포하세요."
