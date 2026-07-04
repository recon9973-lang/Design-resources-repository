-- 원장 앱 RLS(행 수준 보안) 기준선 — Supabase Auth(auth.uid()) 연동 전제.
-- db/schema.sql + db/schema_member_app.sql 적용 후 실행.
--
-- 원칙: deny-by-default. 민감 테이블은 RLS를 켜고, 정책이 없으면 anon/authenticated는
-- 접근 불가. Edge Function은 service_role로 동작하므로 RLS를 우회한다(서버가 기록·판정
-- 주체). 회원이 직접 읽어도 되는 것(자기 자가진단·열람권한)만 소유자 정책을 연다.

-- Supabase Auth(UUID) ↔ 기존 users(BIGSERIAL) 매핑 -------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_uid UUID UNIQUE;

CREATE OR REPLACE FUNCTION app_user_id() RETURNS BIGINT
  LANGUAGE sql STABLE AS $$
  SELECT id FROM users WHERE auth_uid = auth.uid()
$$;

-- 리드 신청 — 서버(service_role)만 기록/조회. anon/authenticated 직접 접근 차단.
ALTER TABLE diagnosis_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE abuse_blocklist    ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppression_list   ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_challenges    ENABLE ROW LEVEL SECURITY;
-- (정책 없음 = service_role 외 접근 불가)

-- 자가진단 결과 — 소유자만 열람/기록/삭제
ALTER TABLE self_check_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY sc_owner_select ON self_check_results
  FOR SELECT USING (user_id = app_user_id());
CREATE POLICY sc_owner_insert ON self_check_results
  FOR INSERT WITH CHECK (user_id = app_user_id());
CREATE POLICY sc_owner_delete ON self_check_results
  FOR DELETE USING (user_id = app_user_id());

-- 리포트 열람 권한 — 소유자만 자기 grant 확인
ALTER TABLE report_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY rg_owner_select ON report_grants
  FOR SELECT USING (user_id = app_user_id());

-- 리포트 본문 — 직접 접근 차단(전체판 유출 방지). masked/full 판정은 Edge Function이
-- report_grants 확인 후 service_role로 수행해 반환한다.
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;
-- (정책 없음 = 직접 SELECT 불가. GET /api/reports/{token} 경유만 허용)

-- users — 본인 행만 조회(관리자단은 service_role/role 기반 별도 처리)
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY users_self_select ON users
  FOR SELECT USING (auth_uid = auth.uid());
