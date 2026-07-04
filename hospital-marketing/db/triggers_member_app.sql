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
