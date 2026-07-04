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
