// 무료 진단 신청(리드) 접수 — Supabase Edge Function (Deno).
//
// 죽어 있던 landing 신청 폼을 실제 저장으로 연결한다. 동의 증적(일시·IP·문구버전)을
// 함께 저장하고, 어뷰징(허니팟·재신청 한도·차단목록)을 서버에서 검사한다.
// service_role로 삽입하므로 RLS를 우회한다(공개 폼이지만 서버가 유일한 기록 주체).
//
// 배포: supabase functions deploy leads --no-verify-jwt
//   (공개 폼이라 JWT 미검증. 남용 방지는 아래 로직 + DB 한도로 처리.)
// 필요 env(자동 주입): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
// 선택 env: ALLOWED_ORIGIN(기본 '*' — 배포 시 진단 서브도메인으로 좁힐 것)

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CONSENT_TEXT_VERSION = "2026-07-v1"; // 동의 문구 버전(문구 변경 시 갱신)

// 재신청 한도 (docs/어뷰징-방지-정책.md · schema.sql 주석과 일치)
const LIMITS = {
  ipPerDay: 3,
  ipPerMonth: 10,
  emailPerMonth: 2,
  phonePerMonth: 3,
  hospitalPerMonth: 1,
};

const cors = (origin: string) => ({
  "Access-Control-Allow-Origin": origin,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
  "Content-Type": "application/json",
});

function json(body: unknown, status: number, origin: string) {
  return new Response(JSON.stringify(body), { status, headers: cors(origin) });
}

const norm = (s: unknown) => String(s ?? "").trim();
const digits = (s: unknown) => norm(s).replace(/[^0-9]/g, "");

Deno.serve(async (req) => {
  const origin = Deno.env.get("ALLOWED_ORIGIN") || "*";
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors(origin) });
  if (req.method !== "POST") return json({ ok: false, reason: "method" }, 405, origin);

  let b: Record<string, unknown>;
  try {
    b = await req.json();
  } catch {
    return json({ ok: false, reason: "bad_json" }, 400, origin);
  }

  // 1) 허니팟 — 봇이 채우는 숨은 필드가 차 있으면 조용히 성공처럼 응답(무저장)
  if (norm(b.company_website)) return json({ ok: true, skipped: true }, 200, origin);

  // 2) 필수값·필수동의 검증
  const hospital_name = norm(b.hospital);
  const hospital_address = norm(b.address);
  const department = norm(b.department);
  const applicant_name = norm(b.name);
  const phone = norm(b.phone);
  const email = norm(b.email).toLowerCase();
  const delivery_method = norm(b.delivery);
  const consent_required = b.consent_required === true;

  if (!hospital_name || !hospital_address || !department || !applicant_name ||
      !phone || !email || !delivery_method) {
    return json({ ok: false, reason: "missing_fields" }, 400, origin);
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return json({ ok: false, reason: "bad_email" }, 400, origin);
  }
  if (!consent_required) {
    return json({ ok: false, reason: "consent_required" }, 400, origin);
  }

  const url = Deno.env.get("SUPABASE_URL")!;
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const db = createClient(url, key);

  // 요청 메타 (동의·어뷰징 증적)
  const fwd = req.headers.get("x-forwarded-for") || "";
  const client_ip = fwd.split(",")[0].trim() || "0.0.0.0";
  const user_agent = req.headers.get("user-agent") || "";
  const phone_d = digits(phone);

  // 3) 차단 목록 — 확정 어뷰징 IP/이메일/전화
  {
    const { data: blocked } = await db
      .from("abuse_blocklist")
      .select("id, kind, identifier, expires_at")
      .or(`and(kind.eq.ip,identifier.eq.${client_ip}),` +
          `and(kind.eq.email,identifier.eq.${email}),` +
          `and(kind.eq.phone,identifier.eq.${phone_d})`);
    const active = (blocked || []).filter(
      (r) => !r.expires_at || new Date(r.expires_at as string) > new Date(),
    );
    if (active.length) return json({ ok: false, reason: "blocked" }, 403, origin);
  }

  // 4) 재신청 한도 — 기간별 카운트(rejected 제외)
  const since = (days: number) =>
    new Date(Date.now() - days * 864e5).toISOString();
  const countWhere = async (col: string, val: string, days: number) => {
    const { count } = await db
      .from("diagnosis_requests")
      .select("id", { count: "exact", head: true })
      .eq(col, val)
      .neq("status", "rejected")
      .gte("created_at", since(days));
    return count || 0;
  };

  if (await countWhere("client_ip", client_ip, 1) >= LIMITS.ipPerDay ||
      await countWhere("client_ip", client_ip, 30) >= LIMITS.ipPerMonth ||
      await countWhere("email", email, 30) >= LIMITS.emailPerMonth ||
      await countWhere("phone", phone, 30) >= LIMITS.phonePerMonth) {
    return json({ ok: false, reason: "rate_limited" }, 429, origin);
  }
  // 동일 병원명+주소 월 1회
  {
    const { count } = await db
      .from("diagnosis_requests")
      .select("id", { count: "exact", head: true })
      .eq("hospital_name", hospital_name)
      .eq("hospital_address", hospital_address)
      .neq("status", "rejected")
      .gte("created_at", since(30));
    if ((count || 0) >= LIMITS.hospitalPerMonth) {
      return json({ ok: false, reason: "duplicate_hospital" }, 429, origin);
    }
  }

  // 5) 저장 — 동의 증적 포함
  const consultRaw = norm(b.consult);
  const { data, error } = await db
    .from("diagnosis_requests")
    .insert({
      hospital_name,
      hospital_address,
      department,
      applicant_name,
      applicant_role: norm(b.role) || null,
      phone,
      email,
      delivery_method,
      keywords: norm(b.keywords) || null,
      consult_wanted: consultRaw.includes("설명"),
      consent_required: true,
      consent_marketing: b.consent_marketing === true,
      consent_ad_sms: b.consent_ad_sms === true,
      consent_ad_email: b.consent_ad_email === true,
      consent_ad_call: b.consent_ad_call === true,
      consent_text_version: CONSENT_TEXT_VERSION,
      client_ip,
      user_agent,
      status: "received",
    })
    .select("id")
    .single();

  if (error) return json({ ok: false, reason: "db_error" }, 500, origin);
  return json({ ok: true, id: data.id }, 200, origin);
});
