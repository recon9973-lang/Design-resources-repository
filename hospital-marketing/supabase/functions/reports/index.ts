// 리포트 게이팅 — Supabase Edge Function (Deno).
//   GET  ?token=…        → 권한 없으면 마킹판(masked), 회원가입·해제(grant) 있으면 전체판(full)
//   POST { token }       → (로그인 필요) 전체공개 해제(grant 생성) 후 전체판 반환
//
// "무료 회원가입 → 나머지도 무료로 본다" 모델. 전체판 유출 방지를 위해 reports 테이블은
// RLS로 직접접근 차단(policies_member_app.sql)하고, 이 함수(service_role)만 masked/full을
// 판정해 내보낸다. 로그인 사용자는 Authorization 헤더의 JWT로 식별한다.
//
// 배포: supabase functions deploy reports   (인증 필요 경로라 verify_jwt 기본값 사용 가능)
// env(자동): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const cors = (origin: string) => ({
  "Access-Control-Allow-Origin": origin,
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, authorization",
  "Content-Type": "application/json",
});
const json = (b: unknown, s: number, o: string) =>
  new Response(JSON.stringify(b), { status: s, headers: cors(o) });

const URL_ = () => Deno.env.get("SUPABASE_URL")!;
const svc = () => createClient(URL_(), Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

// Authorization 헤더의 JWT → 앱 users.id (auth_uid 매핑, 없으면 이메일로 생성)
async function currentAppUser(req: Request): Promise<number | null> {
  const auth = req.headers.get("Authorization");
  if (!auth) return null;
  const anon = createClient(URL_(), Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: auth } },
  });
  const { data: { user } } = await anon.auth.getUser();
  if (!user) return null;
  const db = svc();
  const { data: existing } = await db.from("users").select("id").eq("auth_uid", user.id).maybeSingle();
  if (existing) return existing.id;
  // 최초 로그인 — 앱 users 행 생성(무료 회원가입)
  const { data: created } = await db.from("users")
    .insert({ auth_uid: user.id, email: user.email, role: "customer" })
    .select("id").single();
  return created?.id ?? null;
}

async function serve(reportToken: string, userId: number | null, origin: string) {
  const db = svc();
  const { data: r } = await db.from("reports")
    .select("id, hospital_name, generated_at, masked_html, full_html, masked_storage_path, full_storage_path")
    .eq("public_token", reportToken).maybeSingle();
  if (!r) return json({ ok: false, reason: "not_found" }, 404, origin);

  let unlocked = false;
  if (userId) {
    const { count } = await db.from("report_grants")
      .select("id", { count: "exact", head: true })
      .eq("report_id", r.id).eq("user_id", userId);
    unlocked = (count || 0) > 0;
  }
  return json({
    ok: true, hospital_name: r.hospital_name, generated_at: r.generated_at,
    unlocked,
    html: unlocked ? r.full_html : r.masked_html,
    storage_path: unlocked ? r.full_storage_path : r.masked_storage_path,
  }, 200, origin);
}

Deno.serve(async (req) => {
  const origin = Deno.env.get("ALLOWED_ORIGIN") || "*";
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors(origin) });

  if (req.method === "GET") {
    const token = new URL(req.url).searchParams.get("token") || "";
    if (!token) return json({ ok: false, reason: "missing_token" }, 400, origin);
    const userId = await currentAppUser(req);
    return await serve(token, userId, origin);
  }

  if (req.method === "POST") {           // 전체공개 해제(unlock)
    const userId = await currentAppUser(req);
    if (!userId) return json({ ok: false, reason: "auth_required" }, 401, origin);
    let body: Record<string, unknown>;
    try { body = await req.json(); } catch { return json({ ok: false, reason: "bad_json" }, 400, origin); }
    const token = String(body.token ?? "");
    if (!token) return json({ ok: false, reason: "missing_token" }, 400, origin);
    const db = svc();
    const { data: r } = await db.from("reports").select("id").eq("public_token", token).maybeSingle();
    if (!r) return json({ ok: false, reason: "not_found" }, 404, origin);
    await db.from("report_grants").upsert(
      { report_id: r.id, user_id: userId }, { onConflict: "report_id,user_id" });
    return await serve(token, userId, origin);
  }

  return json({ ok: false, reason: "method" }, 405, origin);
});
