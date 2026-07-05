-- ============================================================
-- 원장 앱 전체 DB 설치 (한 번에 실행용). Supabase SQL Editor에 통째로 붙여넣고 Run.
-- schema.sql + schema_member_app.sql + policies_member_app.sql + triggers_member_app.sql 를
-- 올바른 순서로 합친 파일입니다. (개별 파일은 그대로 유지)
-- ============================================================

-- 필요한 확장 먼저 활성화 (Supabase 지원)
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS postgis;


-- ═══════════════════════════════════════════════════════════
-- ▼▼▼  schema.sql
-- ═══════════════════════════════════════════════════════════

-- 병원 로컬 검색 노출 경쟁력 진단 SaaS
-- PostgreSQL 스키마 초안 (기획서 8장 데이터 모델 구현)
-- 반경 계산은 PostGIS(geography) 기준. PostGIS 미사용 시 lat/lng + 하버사인으로 대체 가능.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------
-- 공통 enum
-- ---------------------------------------------------------------

CREATE TYPE hospital_status AS ENUM ('operating', 'closed', 'suspended', 'unknown');

CREATE TYPE keyword_type AS ENUM (
  'region_department',  -- 지역+진료과 (예: 강남 피부과)
  'procedure',          -- 시술 (예: 리프팅)
  'symptom',            -- 고민/증상 (예: 탈모)
  'comparison',         -- 비교 탐색형
  'brand',              -- 병원 브랜드
  'local_modifier'      -- 역/동 단위 지역 수식어
);

CREATE TYPE risk_level AS ENUM ('low', 'medium', 'high', 'critical');

CREATE TYPE legal_review_status AS ENUM (
  'not_required', 'pending', 'in_review', 'approved', 'rejected'
);

CREATE TYPE collection_mode AS ENUM ('manual', 'semi_automated', 'approved_automated');

CREATE TYPE serp_section_type AS ENUM ('place', 'search_ad', 'organic', 'integrated', 'unknown');

CREATE TYPE evidence_type AS ENUM (
  'raw_api_payload',
  'serp_screenshot',
  'serp_html',
  'ad_section_observation',
  'place_section_observation',
  'collection_error_log',
  'manual_review_note'
);

CREATE TYPE visibility_scope AS ENUM (
  'internal_admin_only',   -- 기본값. 관리자단 전용
  'internal_legal_only',   -- 법무 검토자 전용
  'customer_safe_summary', -- 법무 승인 후 요약값만 고객 노출 가능
  'blocked'                -- 노출/사용 금지
);

CREATE TYPE user_role AS ENUM (
  'customer', 'agency_manager', 'data_operator', 'legal_reviewer', 'admin'
);

CREATE TYPE compliance_status AS ENUM ('safe', 'needs_review', 'blocked');

-- ---------------------------------------------------------------
-- 사용자 / 권한
-- ---------------------------------------------------------------

CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  email         CITEXT UNIQUE NOT NULL,
  password_hash TEXT,
  display_name  TEXT,
  role          user_role NOT NULL DEFAULT 'customer',
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 원천자료 열람/다운로드/삭제 감사 로그 (기획서 11.6)
CREATE TABLE audit_logs (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT REFERENCES users(id),
  action       TEXT NOT NULL,          -- view / download / delete / export ...
  target_table TEXT NOT NULL,
  target_id    BIGINT,
  detail       JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_target ON audit_logs (target_table, target_id);
CREATE INDEX idx_audit_logs_user   ON audit_logs (user_id, created_at);

-- ---------------------------------------------------------------
-- 8.1 hospitals — 병원 마스터 (공공데이터 기반)
-- ---------------------------------------------------------------

CREATE TABLE hospitals (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  address         TEXT,
  road_address    TEXT,
  latitude        DOUBLE PRECISION,
  longitude       DOUBLE PRECISION,
  geom            GEOGRAPHY(Point, 4326),  -- 반경 질의용
  department_code TEXT,
  department_name TEXT,
  source          TEXT NOT NULL,           -- hira / sbiz / naver_local / manual
  source_id       TEXT,
  status          hospital_status NOT NULL DEFAULT 'operating',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, source_id)
);
CREATE INDEX idx_hospitals_geom ON hospitals USING GIST (geom);
CREATE INDEX idx_hospitals_norm_name ON hospitals (normalized_name);
CREATE INDEX idx_hospitals_department ON hospitals (department_code);

-- ---------------------------------------------------------------
-- 8.2 hospital_profiles — 고객이 등록/검증한 내 병원
-- ---------------------------------------------------------------

CREATE TABLE hospital_profiles (
  id                    BIGSERIAL PRIMARY KEY,
  hospital_id           BIGINT NOT NULL REFERENCES hospitals(id),
  owner_user_id         BIGINT NOT NULL REFERENCES users(id),
  naver_place_url       TEXT,
  primary_department    TEXT NOT NULL,
  selected_keywords     BIGINT[] NOT NULL DEFAULT '{}',  -- keywords.id 배열 (요금제별 10/30개 제한은 앱 레이어)
  service_area_radius_m INTEGER NOT NULL DEFAULT 1000
                        CHECK (service_area_radius_m IN (500, 1000, 1500, 2000)),
  verified_at           TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_profiles_owner ON hospital_profiles (owner_user_id);

-- ---------------------------------------------------------------
-- 8.3 competitors — 월 스냅샷 단위 반경 내 경쟁 세트
-- ---------------------------------------------------------------

CREATE TABLE competitors (
  id                       BIGSERIAL PRIMARY KEY,
  base_hospital_id         BIGINT NOT NULL REFERENCES hospital_profiles(id),
  competitor_hospital_id   BIGINT NOT NULL REFERENCES hospitals(id),
  radius_m                 INTEGER NOT NULL CHECK (radius_m IN (500, 1000, 1500, 2000)),
  distance_m               DOUBLE PRECISION NOT NULL,
  department_similarity    NUMERIC(3,2) NOT NULL DEFAULT 1.00,  -- 같은과 1.0 / 유사과 0.5-0.8
  keyword_overlap_score    NUMERIC(4,3) NOT NULL DEFAULT 0,
  competition_weight       NUMERIC(6,3) NOT NULL DEFAULT 0,
  snapshot_month           DATE NOT NULL,                       -- 매월 1일로 정규화
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (base_hospital_id, competitor_hospital_id, radius_m, snapshot_month)
);
CREATE INDEX idx_competitors_base ON competitors (base_hospital_id, snapshot_month, radius_m);

-- ---------------------------------------------------------------
-- 8.4 keywords — 진료과별 키워드 사전 (수동 관리)
-- ---------------------------------------------------------------

CREATE TABLE keywords (
  id           BIGSERIAL PRIMARY KEY,
  department   TEXT NOT NULL,
  keyword      TEXT NOT NULL,
  keyword_type keyword_type NOT NULL,
  priority     SMALLINT NOT NULL DEFAULT 100,
  risk_level   risk_level NOT NULL DEFAULT 'low',
  active       BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (department, keyword)
);

-- ---------------------------------------------------------------
-- 8.5 search_api_results — 네이버 공식 API 관찰값
-- ---------------------------------------------------------------

CREATE TABLE search_api_results (
  id                  BIGSERIAL PRIMARY KEY,
  hospital_profile_id BIGINT NOT NULL REFERENCES hospital_profiles(id),
  keyword_id          BIGINT NOT NULL REFERENCES keywords(id),
  provider            TEXT NOT NULL DEFAULT 'naver',
  api_name            TEXT NOT NULL,                 -- local_search 등
  collected_at        TIMESTAMPTZ NOT NULL,
  rank_position       INTEGER,                       -- NULL = API 결과 내 미노출
  result_title        TEXT,
  result_address      TEXT,
  result_url          TEXT,
  matched_hospital_id BIGINT REFERENCES hospitals(id),
  confidence_score    NUMERIC(4,3),
  raw_payload_ref     TEXT                           -- internal_evidence_items.storage_ref 참조 키
);
CREATE INDEX idx_api_results_profile
  ON search_api_results (hospital_profile_id, keyword_id, collected_at);

-- ---------------------------------------------------------------
-- 8.6 serp_snapshots — 월간 검증 스냅샷 (B-safe, 법무 검토 후 운영)
-- ---------------------------------------------------------------

CREATE TABLE serp_snapshots (
  id                  BIGSERIAL PRIMARY KEY,
  hospital_profile_id BIGINT NOT NULL REFERENCES hospital_profiles(id),
  keyword_id          BIGINT NOT NULL REFERENCES keywords(id),
  collected_at        TIMESTAMPTZ NOT NULL,
  collection_mode     collection_mode NOT NULL DEFAULT 'manual',
  device_type         TEXT NOT NULL DEFAULT 'mobile',   -- mobile / desktop
  location_basis      TEXT,                             -- 수집 기준 위치 설명
  login_state         BOOLEAN NOT NULL DEFAULT FALSE,   -- 로그인 수집 금지: FALSE 고정 운영
  rank_position       INTEGER,
  section_type        serp_section_type NOT NULL DEFAULT 'unknown',
  evidence_ref        TEXT,                             -- 원본 증빙은 internal_evidence_items에만
  legal_review_status legal_review_status NOT NULL DEFAULT 'pending',
  CHECK (login_state = FALSE)                           -- 로그인/개인화 결과 사용 금지 (기획서 11.2)
);
CREATE INDEX idx_serp_snapshots_profile
  ON serp_snapshots (hospital_profile_id, keyword_id, collected_at);

-- ---------------------------------------------------------------
-- 8.7 population_area_metrics — SGIS 인구/공간 지표
-- ---------------------------------------------------------------

CREATE TABLE population_area_metrics (
  id                BIGSERIAL PRIMARY KEY,
  area_code         TEXT NOT NULL,
  area_type         TEXT NOT NULL,          -- adm_dong / census_block
  geometry_ref      TEXT,
  geom              GEOGRAPHY(MultiPolygon, 4326),
  total_population  INTEGER,
  age_band_metrics  JSONB,                  -- {"20s": n, "30s": n, ...}
  source            TEXT NOT NULL DEFAULT 'sgis',
  source_updated_at DATE,
  UNIQUE (area_code, area_type)
);
CREATE INDEX idx_population_geom ON population_area_metrics USING GIST (geom);

-- ---------------------------------------------------------------
-- 8.8 radius_metrics — 월간 반경별 점수 (대시보드 캐시)
-- ---------------------------------------------------------------

CREATE TABLE radius_metrics (
  id                         BIGSERIAL PRIMARY KEY,
  hospital_profile_id        BIGINT NOT NULL REFERENCES hospital_profiles(id),
  radius_m                   INTEGER NOT NULL CHECK (radius_m IN (500, 1000, 1500, 2000)),
  snapshot_month             DATE NOT NULL,
  competitor_count           INTEGER NOT NULL DEFAULT 0,
  weighted_competition_score NUMERIC(5,2),  -- 경쟁 밀도 25%
  population_score           NUMERIC(5,2),
  demand_score               NUMERIC(5,2),  -- 수요/입지 20%
  exposure_score             NUMERIC(5,2),  -- 노출 40%
  place_quality_score        NUMERIC(5,2),  -- 플레이스 품질 15%
  final_marketing_score      NUMERIC(5,2) CHECK (final_marketing_score BETWEEN 0 AND 100),
  UNIQUE (hospital_profile_id, radius_m, snapshot_month)
);

-- ---------------------------------------------------------------
-- 8.9 action_recommendations — 월간 개선 액션 (컴플라이언스 게이트 포함)
-- ---------------------------------------------------------------

CREATE TABLE action_recommendations (
  id                  BIGSERIAL PRIMARY KEY,
  hospital_profile_id BIGINT NOT NULL REFERENCES hospital_profiles(id),
  snapshot_month      DATE NOT NULL,
  priority            SMALLINT NOT NULL,           -- 1이 최우선, 고객 화면엔 상위 3개
  action_type         TEXT NOT NULL,               -- place_info / keyword_coverage / review_process ...
  title               TEXT NOT NULL,
  explanation         TEXT,
  evidence_metric     JSONB,                       -- 근거 지표 (점수/키워드 등급 등 안전값만)
  compliance_status   compliance_status NOT NULL DEFAULT 'needs_review'
);
CREATE INDEX idx_actions_profile ON action_recommendations (hospital_profile_id, snapshot_month, priority);

-- ---------------------------------------------------------------
-- 8.10 internal_evidence_items — 관리자 전용 고위험 원천자료
-- ---------------------------------------------------------------

CREATE TABLE internal_evidence_items (
  id                  BIGSERIAL PRIMARY KEY,
  hospital_profile_id BIGINT REFERENCES hospital_profiles(id),
  keyword_id          BIGINT REFERENCES keywords(id),
  evidence_type       evidence_type NOT NULL,
  source_provider     TEXT,
  collected_at        TIMESTAMPTZ NOT NULL,
  risk_level          risk_level NOT NULL DEFAULT 'medium',
  legal_review_status legal_review_status NOT NULL DEFAULT 'pending',
  visibility_scope    visibility_scope NOT NULL DEFAULT 'internal_admin_only',
  storage_ref         TEXT NOT NULL,               -- 오브젝트 스토리지 키. 고객 응답에 절대 포함 금지
  redacted_summary    JSONB,                       -- 고객용 지표 생성에 쓸 수 있는 유일한 필드
  collection_context  JSONB,                       -- 위치/시간/기기/로그인 여부 등 메타
  reviewed_by         BIGINT REFERENCES users(id),
  reviewed_at         TIMESTAMPTZ
);
CREATE INDEX idx_evidence_profile ON internal_evidence_items (hospital_profile_id, collected_at);
CREATE INDEX idx_evidence_review  ON internal_evidence_items (legal_review_status, risk_level);

-- 법무 승인 전 customer_safe_summary 승격 금지 (기획서 8.10 원칙)
CREATE OR REPLACE FUNCTION enforce_evidence_visibility() RETURNS trigger AS $$
BEGIN
  IF NEW.visibility_scope = 'customer_safe_summary'
     AND NEW.legal_review_status <> 'approved' THEN
    RAISE EXCEPTION 'customer_safe_summary requires legal_review_status = approved';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_evidence_visibility
  BEFORE INSERT OR UPDATE ON internal_evidence_items
  FOR EACH ROW EXECUTE FUNCTION enforce_evidence_visibility();

-- ---------------------------------------------------------------
-- 무료 진단 신청 접수 + 어뷰징 방지 (docs/어뷰징-방지-정책.md)
-- ---------------------------------------------------------------

CREATE TYPE request_status AS ENUM (
  'received',      -- 접수
  'generating',    -- 리포트 생성 중
  'sent',          -- 발송 완료
  'consulting',    -- 상담 진행
  'converted',     -- 광고대행 계약 전환
  'rejected',      -- 어뷰징/중복 등으로 반려
  'blocked'        -- 차단 (재신청 불가)
);

-- 신청 1건 = 1행. 동의 증적(일시/IP/문구 버전)을 함께 보관 (개인정보보호법 대응)
CREATE TABLE diagnosis_requests (
  id                  BIGSERIAL PRIMARY KEY,
  hospital_name       TEXT NOT NULL,
  hospital_address    TEXT NOT NULL,
  department          TEXT NOT NULL,
  applicant_name      TEXT NOT NULL,
  applicant_role      TEXT,
  phone               TEXT NOT NULL,
  email               CITEXT NOT NULL,
  delivery_method     TEXT NOT NULL,             -- email / sms / both
  keywords            TEXT,
  consult_wanted      BOOLEAN NOT NULL DEFAULT FALSE,
  -- 동의 증적
  consent_required    BOOLEAN NOT NULL,          -- 필수 2건 (수집·이용 + 제공조건)
  consent_marketing   BOOLEAN NOT NULL DEFAULT FALSE,
  consent_ad_sms      BOOLEAN NOT NULL DEFAULT FALSE,
  consent_ad_email    BOOLEAN NOT NULL DEFAULT FALSE,
  consent_ad_call     BOOLEAN NOT NULL DEFAULT FALSE,
  consent_text_version TEXT NOT NULL,            -- 동의 문구 버전 (예: 2026-07-v1)
  -- 어뷰징 방지용 요청 메타 (처리방침 "자동 수집 항목"에 고지됨)
  client_ip           INET NOT NULL,
  user_agent          TEXT,
  status              request_status NOT NULL DEFAULT 'received',
  reject_reason       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_requests_ip_time    ON diagnosis_requests (client_ip, created_at);
CREATE INDEX idx_requests_email_time ON diagnosis_requests (email, created_at);
CREATE INDEX idx_requests_hospital   ON diagnosis_requests (hospital_name, hospital_address);
CREATE INDEX idx_requests_status     ON diagnosis_requests (status, created_at);

-- 요청 제한 정책 (애플리케이션 레이어에서 접수 전 검사, 기본값)
--   IP당:     일 3회 / 월 10회 초과 시 자동 반려(rejected) + 검토 큐
--   이메일당: 월 2회 초과 시 반려
--   병원당:   동일 병원명+주소는 월 1회 (갱신 리포트는 익월부터)
--   전화번호당: 월 3회 초과 시 반려
-- 임계 2배 초과 또는 반려 3회 누적 시 blocked 전환(관리자 해제 전 재신청 불가)

CREATE OR REPLACE FUNCTION count_recent_requests(
  p_ip INET, p_hours INTEGER
) RETURNS BIGINT AS $$
  SELECT count(*) FROM diagnosis_requests
  WHERE client_ip = p_ip
    AND created_at > now() - make_interval(hours => p_hours)
    AND status <> 'rejected';
$$ LANGUAGE sql STABLE;

-- 수신거부 / 재연락 금지 목록 (정보통신망법 §50, 어떤 캠페인에서도 제외)
CREATE TABLE suppression_list (
  id           BIGSERIAL PRIMARY KEY,
  channel      TEXT NOT NULL CHECK (channel IN ('sms', 'email', 'call', 'all')),
  identifier   TEXT NOT NULL,                    -- 전화번호 또는 이메일
  reason       TEXT NOT NULL,                    -- opt_out / cold_call_refusal / admin
  source       TEXT,                             -- 철회 접수 경로
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel, identifier)
);

-- 차단 IP/식별자 (어뷰징 확정 건)
CREATE TABLE abuse_blocklist (
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('ip', 'email', 'phone')),
  identifier   TEXT NOT NULL,
  reason       TEXT NOT NULL,
  blocked_by   BIGINT REFERENCES users(id),
  expires_at   TIMESTAMPTZ,                      -- NULL = 무기한 (관리자 해제 전)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (kind, identifier)
);

-- ---------------------------------------------------------------
-- 반경 내 경쟁 병원 조회 헬퍼
-- ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION find_competitors_in_radius(
  p_hospital_id BIGINT,
  p_radius_m    INTEGER
) RETURNS TABLE (hospital_id BIGINT, distance_m DOUBLE PRECISION) AS $$
  SELECT h.id, ST_Distance(base.geom, h.geom) AS distance_m
  FROM hospitals base
  JOIN hospitals h
    ON h.id <> base.id
   AND h.status = 'operating'
   AND ST_DWithin(base.geom, h.geom, p_radius_m)
  WHERE base.id = p_hospital_id
  ORDER BY distance_m;
$$ LANGUAGE sql STABLE;

-- ═══════════════════════════════════════════════════════════
-- ▼▼▼  schema_member_app.sql
-- ═══════════════════════════════════════════════════════════

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

-- ═══════════════════════════════════════════════════════════
-- ▼▼▼  policies_member_app.sql
-- ═══════════════════════════════════════════════════════════

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

-- ═══════════════════════════════════════════════════════════
-- ▼▼▼  triggers_member_app.sql
-- ═══════════════════════════════════════════════════════════

-- 원장 앱 트리거 — Supabase Auth 연동. schema.sql + schema_member_app.sql + policies 적용 후 실행.
-- (auth 스키마는 Supabase 전용이라 로컬 vanilla Postgres에는 없음 — Supabase에서 실행)

-- 무료 회원가입: Supabase Auth 사용자 생성 시 앱 users 행 자동 생성(auth_uid 매핑).
CREATE OR REPLACE FUNCTION handle_new_auth_user() RETURNS TRIGGER
  LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.users (auth_uid, email, role)
  VALUES (NEW.id, NEW.email, 'customer')
  ON CONFLICT (auth_uid) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_auth_user();

-- updated_at 자동 갱신(공통) — users 등에 적용
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS trg_users_touch ON users;
CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
