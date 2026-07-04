-- 원장(고객) 앱 전용 스키마 델타 — db/schema.sql 을 확장(additive).
-- 기존 파일은 건드리지 않는다. 리드(diagnosis_requests)·회원(users)·어뷰징
-- (abuse_blocklist/suppression_list)은 그대로 재사용하고, 여기서는 원장 앱에만
-- 필요한 4가지(무비밀번호 인증·자가진단 저장·리포트 레지스트리·열람 해제)를 추가한다.
--
-- 전제: db/schema.sql 이 먼저 적용돼 users 테이블과 CITEXT/pgcrypto가 있어야 한다.
-- RLS(Row Level Security)는 Supabase 등 auth.uid() 연동 시 활성화(하단 주석 참고).

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid, digest

-- ---------------------------------------------------------------
-- M.1 auth_challenges — magic-link / OTP (무비밀번호 회원가입·로그인)
--   이메일 소유 확인만으로 무료 회원가입. 비밀번호 미보관(users.password_hash NULL 허용).
-- ---------------------------------------------------------------
CREATE TYPE auth_purpose AS ENUM ('signup', 'login', 'report_unlock');

CREATE TABLE auth_challenges (
  id           BIGSERIAL PRIMARY KEY,
  email        CITEXT NOT NULL,
  purpose      auth_purpose NOT NULL,
  token_hash   TEXT NOT NULL,               -- 원문 토큰은 저장 안 함(sha256 해시만)
  code_hash    TEXT,                        -- OTP 6자리 해시(선택)
  redirect_to  TEXT,                        -- 인증 후 돌아갈 경로(리포트 토큰 등)
  client_ip    INET,
  consumed_at  TIMESTAMPTZ,                 -- 1회용 — 사용 시각 기록
  expires_at   TIMESTAMPTZ NOT NULL,        -- 보통 발급 후 15분
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_auth_ch_email  ON auth_challenges (email, created_at);
CREATE INDEX idx_auth_ch_expiry ON auth_challenges (expires_at);
-- 발급 남용 방지: 이메일/IP당 분당 한도는 앱 레이어에서 검사(abuse 정책 준용).

-- ---------------------------------------------------------------
-- M.2 self_check_results — 자가진단(리스크·비용·동선) 결과 저장·재열람
--   계산 자체는 클라이언트에서 수행. 회원이 "저장"을 눌러야만 서버로 전송·보관.
-- ---------------------------------------------------------------
CREATE TYPE self_check_kind AS ENUM ('risk', 'cost', 'journey');

CREATE TABLE self_check_results (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         self_check_kind NOT NULL,
  score        NUMERIC,                     -- 도구별 대표 점수(선택)
  inputs       JSONB NOT NULL,              -- 사용자가 입력한 값
  outputs      JSONB NOT NULL,              -- 계산 결과(등급·항목별)
  tool_version TEXT NOT NULL,               -- 계산 로직 버전(재현성)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_selfcheck_user ON self_check_results (user_id, created_at);

-- ---------------------------------------------------------------
-- M.3 reports — 생성된 진단 리포트 레지스트리(마킹판/전체판 게이팅)
--   auto_diagnose 산출물을 등록. 익명은 masked, 회원가입·해제 후 full 열람.
--   본문은 Storage(경로) 또는 DB(html)에 보관 — 둘 다 지원.
-- ---------------------------------------------------------------
CREATE TABLE reports (
  id                BIGSERIAL PRIMARY KEY,
  public_token      TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex'),
  hospital_name     TEXT NOT NULL,
  hospital_address  TEXT,
  request_id        BIGINT REFERENCES diagnosis_requests(id),  -- 어떤 신청으로 생성됐나(있으면)
  masked_storage_path TEXT,               -- 마킹판 위치(Storage) 또는
  full_storage_path   TEXT,               -- 전체판 위치(Storage)
  masked_html       TEXT,                 -- (대안) 마킹판 인라인 보관
  full_html         TEXT,                 -- (대안) 전체판 인라인 보관
  generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at        TIMESTAMPTZ           -- 열람 만료(선택)
);
CREATE INDEX idx_reports_hospital ON reports (hospital_name, hospital_address);
CREATE INDEX idx_reports_request  ON reports (request_id);

-- ---------------------------------------------------------------
-- M.4 report_grants — 회원 → 리포트 전체공개 열람 해제(마킹 해제)
--   "무료 회원가입 시 나머지도 무료로 본다" 모델의 권한 행.
-- ---------------------------------------------------------------
CREATE TABLE report_grants (
  id           BIGSERIAL PRIMARY KEY,
  report_id    BIGINT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (report_id, user_id)
);
CREATE INDEX idx_grants_user ON report_grants (user_id);

-- ---------------------------------------------------------------
-- RLS 정책 개요 (Supabase auth 연동 시 활성화)
--   users.id ↔ auth.uid() 매핑을 전제로:
--   - self_check_results / report_grants : user_id = 현재 사용자만 SELECT/INSERT
--   - reports : masked_* 는 공개 토큰으로 조회 가능, full_* 는 report_grants 존재 시만
--   - diagnosis_requests : customer는 INSERT만(본인 신청), 조회·상태변경은 관리자 role
--   - data_operator/legal_reviewer/admin : 서버(service_role) 경유로만 접근 + audit_logs 기록
-- 예시(개념):
--   ALTER TABLE self_check_results ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY sc_owner ON self_check_results
--     USING (user_id = current_app_user_id());   -- auth.uid() 매핑 함수
-- ---------------------------------------------------------------
