// 자가진단 결과 저장·조회 — Supabase Edge Function (Deno). 로그인(회원) 전용.
//   POST { kind, score?, inputs, outputs, tool_version } → 저장(self_check_results)
//   GET                                                  → 내 저장 결과 목록(최근 20)
// 계산은 클라이언트에서 수행하고, 회원이 "저장"을 눌렀을 때만 서버로 온다(최소수집).
// 배포: supabase functions deploy self-check
// env(자동): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const KINDS = ["risk", "cost", "journey"];
const cors = (o: string) => ({
  "Access-Control-Allow-Origin": o,
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, authorization",
  "Content-Type": "application/json",
});
const json = (b: unknown, s: number, o: string) =>
  new Response(JSON.stringify(b), { status: s, headers: cors(o) });
const URL_ = () => Deno.env.get("SUPABASE_URL")!;
const svc = () => createClient(URL_(), Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

async function currentAppUser(req: Request): Promise<number | null> {
  const auth = req.headers.get("Authorization");
  if (!auth) return null;
  const anon = createClient(URL_(), Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: auth } },
  });
  const { data: { user } } = await anon.auth.getUser();
  if (!user) return null;
  const db = svc();
  const { data: ex } = await db.from("users").select("id").eq("auth_uid", user.id).maybeSingle();
  if (ex) return ex.id;
  const { data: cr } = await db.from("users")
    .insert({ auth_uid: user.id, email: user.email, role: "customer" }).select("id").single();
  return cr?.id ?? null;
}

Deno.serve(async (req) => {
  const origin = Deno.env.get("ALLOWED_ORIGIN") || "*";
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors(origin) });

  const userId = await currentAppUser(req);
  if (!userId) return json({ ok: false, reason: "auth_required" }, 401, origin);
  const db = svc();

  if (req.method === "GET") {
    const { data } = await db.from("self_check_results")
      .select("id, kind, score, outputs, tool_version, created_at")
      .eq("user_id", userId).order("created_at", { ascending: false }).limit(20);
    return json({ ok: true, results: data ?? [] }, 200, origin);
  }

  if (req.method === "POST") {
    let b: Record<string, unknown>;
    try { b = await req.json(); } catch { return json({ ok: false, reason: "bad_json" }, 400, origin); }
    const kind = String(b.kind ?? "");
    if (!KINDS.includes(kind)) return json({ ok: false, reason: "bad_kind" }, 400, origin);
    if (typeof b.inputs !== "object" || typeof b.outputs !== "object")
      return json({ ok: false, reason: "missing_payload" }, 400, origin);
    const score = (b.score === null || b.score === undefined) ? null : Number(b.score);
    const { data, error } = await db.from("self_check_results").insert({
      user_id: userId, kind, score,
      inputs: b.inputs, outputs: b.outputs,
      tool_version: String(b.tool_version ?? "sc-2026-07-v1"),
    }).select("id").single();
    if (error) return json({ ok: false, reason: "db_error" }, 500, origin);
    return json({ ok: true, id: data.id }, 200, origin);
  }

  return json({ ok: false, reason: "method" }, 405, origin);
});
