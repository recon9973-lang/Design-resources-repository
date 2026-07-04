// 관리자 차트룸 — 리드 조회·상태변경 (Supabase Edge Function, Deno). 베놈 직원 전용.
//   GET               → diagnosis_requests 목록(최근순, 최대 200)
//   PATCH { id,status }→ 신청 상태 변경(퍼널 이동)
// 접근 통제: 호출자 JWT → users.role 이 직원(admin/agency_manager/data_operator)일 때만.
// 열람·변경은 audit_logs에 기록. 배포: supabase functions deploy admin-leads
// env(자동): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const STAFF = ["admin", "agency_manager", "data_operator"];
const STATUSES = ["received","generating","sent","consulting","converted","rejected","blocked"];
const cors = (o: string) => ({
  "Access-Control-Allow-Origin": o,
  "Access-Control-Allow-Methods": "GET, PATCH, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, authorization",
  "Content-Type": "application/json",
});
const json = (b: unknown, s: number, o: string) =>
  new Response(JSON.stringify(b), { status: s, headers: cors(o) });
const URL_ = () => Deno.env.get("SUPABASE_URL")!;
const svc = () => createClient(URL_(), Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

// JWT → { id, role } (직원 여부 판정). 로그인/미직원이면 null.
async function staffUser(req: Request): Promise<{ id: number; role: string } | null> {
  const auth = req.headers.get("Authorization");
  if (!auth) return null;
  const anon = createClient(URL_(), Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: auth } },
  });
  const { data: { user } } = await anon.auth.getUser();
  if (!user) return null;
  const { data: u } = await svc().from("users").select("id, role").eq("auth_uid", user.id).maybeSingle();
  if (!u || !STAFF.includes(u.role)) return null;
  return u;
}

async function audit(actor: number, action: string, targetId: number | null, detail: unknown) {
  try { await svc().from("audit_logs").insert({
    user_id: actor, action, target_table: "diagnosis_requests", target_id: targetId, detail }); } catch { /* best-effort */ }
}

Deno.serve(async (req) => {
  const origin = Deno.env.get("ALLOWED_ORIGIN") || "*";
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors(origin) });

  const staff = await staffUser(req);
  if (!staff) return json({ ok: false, reason: "forbidden" }, 403, origin);
  const db = svc();

  if (req.method === "GET") {
    const { data } = await db.from("diagnosis_requests")
      .select("id, created_at, hospital_name, hospital_address, applicant_name, applicant_role, " +
              "delivery_method, consult_wanted, consent_ad_sms, consent_ad_email, consent_ad_call, " +
              "status, reject_reason")
      .order("created_at", { ascending: false }).limit(200);
    await audit(staff.id, "view", null, { count: data?.length ?? 0 });
    return json({ ok: true, leads: data ?? [] }, 200, origin);
  }

  if (req.method === "PATCH") {
    let b: Record<string, unknown>;
    try { b = await req.json(); } catch { return json({ ok: false, reason: "bad_json" }, 400, origin); }
    const id = Number(b.id); const status = String(b.status ?? "");
    if (!id || !STATUSES.includes(status)) return json({ ok: false, reason: "bad_input" }, 400, origin);
    const { error } = await db.from("diagnosis_requests")
      .update({ status, updated_at: new Date().toISOString() }).eq("id", id);
    if (error) return json({ ok: false, reason: "db_error" }, 500, origin);
    await audit(staff.id, "update_status", id, { status });
    return json({ ok: true }, 200, origin);
  }

  return json({ ok: false, reason: "method" }, 405, origin);
});
