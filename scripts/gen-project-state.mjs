#!/usr/bin/env node
// PROJECT_STATE 자동 생성기 — 저장소를 스캔해 압축 현황 1파일(PROJECT_STATE.md)을 만든다.
// 목적: 새 세션이 수십 번 grep/read 하던 "재탐색"을 1파일 읽기로 대체 → 토큰 급감.
// 무의존(Node 내장만). 어느 저장소 루트에서 실행해도 동작(모든 프로젝트 공용 드롭인).
// 사용:  node scripts/gen-project-state.mjs   (repo 루트에서)

import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const sh = (c) => { try { return execSync(c, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(); } catch { return ""; } };
const read = (p) => { try { return fs.readFileSync(path.join(ROOT, p), "utf8"); } catch { return ""; } };
const exists = (p) => fs.existsSync(path.join(ROOT, p));

// ── 저장소 기본 정보 ──
const remote = sh("git config --get remote.origin.url");
const repoName = (remote.match(/([^/]+?)(?:\.git)?$/) || [])[1] || path.basename(ROOT);
const branch = sh("git rev-parse --abbrev-ref HEAD") || "?";
const defaultBranch = sh("git symbolic-ref --short refs/remotes/origin/HEAD").replace("origin/", "") || branch;

// ── 최근 커밋 ──
const commits = sh('git log -8 --pretty=format:"- %ad %s" --date=short').split("\n").filter(Boolean);

// ── 워크플로 + 스케줄 ──
const wfDir = ".github/workflows";
const workflows = [];
if (exists(wfDir)) {
  for (const f of fs.readdirSync(path.join(ROOT, wfDir)).filter((f) => /\.ya?ml$/.test(f))) {
    const c = read(`${wfDir}/${f}`);
    const cron = (c.match(/cron:\s*['"][^'"]+['"]/g) || []).map((s) => s.replace(/cron:\s*/, "")).join(", ");
    const disp = /workflow_dispatch/.test(c) ? "수동" : "";
    workflows.push(`- \`${f}\`${cron ? ` · ${cron}` : ""}${disp ? ` · ${disp}` : ""}`);
  }
}

// ── vercel.json crons ──
let vercelCrons = [];
for (const p of ["vercel.json", "venom-wordpress/preview/vercel.json"]) {
  const c = read(p);
  if (c) { try { const j = JSON.parse(c); (j.crons || []).forEach((cr) => vercelCrons.push(`- \`${cr.path}\` · ${cr.schedule}`)); } catch {} }
}

// ── package.json 스크립트 ──
const pkgRaw = read("package.json") || read("venom-wordpress/preview/package.json");
let scripts = [], deps = 0;
if (pkgRaw) { try { const j = JSON.parse(pkgRaw); scripts = Object.keys(j.scripts || {}); deps = Object.keys(j.dependencies || {}).length; } catch {} }

// ── API 엔드포인트 (api/*.js, app/**/route.ts) ──
const endpoints = [];
function walk(dir, depth = 0) {
  if (depth > 6) return;
  let entries; try { entries = fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    if (e.name === "node_modules" || e.name === ".next" || e.name === ".git") continue;
    const rel = `${dir}/${e.name}`;
    if (e.isDirectory()) walk(rel, depth + 1);
    else if (/\/api\/.*\.(js|ts)$/.test(rel) && !/\.test\./.test(rel)) endpoints.push(rel.replace(/^\.\//, ""));
    else if (/route\.(ts|js)$/.test(e.name)) endpoints.push(rel.replace(/^\.\//, ""));
  }
}
walk(".");

// ── 환경변수 표면(값 아님, 이름만) ──
const envNames = new Set();
const grepEnv = sh(`grep -rhoE "process\\.env\\.[A-Z0-9_]+" --include=*.js --include=*.ts --include=*.mjs . 2>/dev/null | sort -u`);
grepEnv.split("\n").forEach((l) => { const m = l.match(/process\.env\.([A-Z0-9_]+)/); if (m) envNames.add(m[1]); });

// ── 알려진 이슈/이어갈 작업 (있으면 포함) ──
const resume = exists("RESUME.md") ? "있음 → `RESUME.md` 참조" : "없음";

// ── 출력 ──
const cap = (arr, n) => arr.length > n ? arr.slice(0, n).concat([`…(+${arr.length - n})`]) : arr;
const out = `# PROJECT_STATE — ${repoName}

> 🤖 자동 생성 파일. 직접 수정 금지 — \`node scripts/gen-project-state.mjs\`(또는 CI)가 push마다 갱신.
> **새 세션은 이 파일부터 읽어 재탐색 토큰을 아낀다.**

- **저장소**: ${repoName}  ·  **현재 브랜치**: ${branch}  ·  **기본 브랜치**: ${defaultBranch}
- **이어갈 작업(RESUME)**: ${resume}

## 최근 커밋 (8)
${commits.join("\n") || "- (없음)"}

## 워크플로 (${workflows.length})
${workflows.join("\n") || "- (없음)"}

## Vercel crons
${vercelCrons.join("\n") || "- (없음)"}

## API 엔드포인트 (${endpoints.length})
${cap(endpoints, 30).map((e) => `- \`${e}\``).join("\n") || "- (없음)"}

## package 스크립트
${scripts.map((s) => `\`${s}\``).join(" · ") || "(없음)"}  ·  deps ${deps}개

## 환경변수 표면 (이름만, 값 아님 · ${envNames.size})
${cap([...envNames].sort(), 40).map((e) => `\`${e}\``).join(" · ") || "(없음)"}

---
*생성: 커밋 ${sh("git rev-parse --short HEAD") || "?"} 기준. 값·비밀은 포함하지 않음.*
`;

fs.writeFileSync(path.join(ROOT, "PROJECT_STATE.md"), out);
console.log(`✅ PROJECT_STATE.md 생성 (${repoName}) — 엔드포인트 ${endpoints.length}, 워크플로 ${workflows.length}, env ${envNames.size}`);
