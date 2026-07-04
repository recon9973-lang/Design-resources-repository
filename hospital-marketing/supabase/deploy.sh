#!/usr/bin/env bash
# 원장 앱 원샷 배포 — 스키마 4종 적용 + Edge Functions 4종 배포.
#
# 사전 준비(최초 1회):
#   1) Supabase 프로젝트 생성 (supabase.com)
#   2) supabase CLI 설치 + 로그인:  npm i -g supabase && supabase login
#   3) 프로젝트 링크:               supabase link --project-ref <PROJECT_REF>
#   4) DB 접속 문자열 export:       export DATABASE_URL="postgresql://postgres:<PW>@db.<REF>.supabase.co:5432/postgres"
#      (Supabase 대시보드 → Project Settings → Database → Connection string)
#
# 실행:  bash supabase/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # → hospital-marketing/

command -v supabase >/dev/null || { echo "❌ supabase CLI 없음. npm i -g supabase"; exit 1; }
command -v psql >/dev/null || { echo "❌ psql 없음(스키마 적용용). postgresql-client 설치"; exit 1; }
: "${DATABASE_URL:?❌ DATABASE_URL 환경변수를 설정하세요(위 4번)}"

echo "[1/2] DB 스키마 적용 (순서 중요)"
for f in db/schema.sql db/schema_member_app.sql db/policies_member_app.sql db/triggers_member_app.sql; do
  echo "  → $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$f"
done

echo "[2/2] Edge Functions 배포"
for fn in leads reports self-check admin-leads; do
  echo "  → $fn"
  supabase functions deploy "$fn"
done

REF="$(supabase status 2>/dev/null | sed -n 's/.*https:\/\/\([a-z0-9]*\)\.supabase\.co.*/\1/p' | head -1)"
echo ""
echo "✅ 배포 완료."
echo "함수 URL 베이스: https://<PROJECT_REF>.supabase.co/functions/v1/"
echo ""
echo "다음: 프론트 엔드포인트 상수 채우기 →"
echo "  bash supabase/set-endpoints.sh <PROJECT_REF> <ANON_KEY>"
