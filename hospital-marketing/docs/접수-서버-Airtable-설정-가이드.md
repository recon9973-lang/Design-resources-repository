# 무료 진단 신청 접수 — Airtable 연동 설정 가이드

> 작성 2026-07-06 · 내부용. 신청 폼(landing) 제출 → Airtable "진단 신청" 테이블 자동 인입.

## 구조 (한눈에)

```
신청자 → landing/index.html (GitHub Pages, 정적)
            │  POST (JSON)
            ▼
   /api/apply  (Vercel 서버리스, 베놈 사이트)   ← Airtable 토큰은 여기에만 있음(안전)
            │  Airtable REST API
            ▼
   Airtable "베놈 체크업 — 진단 신청 접수" 베이스 / "진단 신청" 테이블
```

- 정적 페이지에 토큰을 넣으면 누구나 훔쳐 쓸 수 있으므로, **토큰은 Vercel 서버(환경변수)에만** 둡니다.
- 폼 제출 실패 시에도 동의 증적은 브라우저에 임시 보관(`venom_consent_log`)되어 유실되지 않습니다.

## 이미 만들어 둔 것

| 항목 | 값 |
|---|---|
| Airtable 베이스 ID | `appvjDAassfO6Q39W` |
| 테이블 이름 | `진단 신청` (ID `tbl4dmJ4U1a4gybw4`) |
| 접수 엔드포인트(코드) | `desktop-tutorial` 저장소 `venom-wordpress/preview/api/apply.js` |
| 폼 연결 | `hospital-marketing/landing/index.html` → `APPLY_ENDPOINT` 상수 |
| 필드 | 병원명·연락처·동의 4종·광고채널·동의문구버전·IP·처리상태 등 24개 (증적 포함) |

테이블에는 필드 매핑 검증용 `[테스트]` 레코드 1건이 들어 있을 수 있습니다 — 확인 후 지우세요.

## 대표님이 하실 일 (5분)

### 1. Airtable 개인 액세스 토큰 발급
1. https://airtable.com/create/tokens 접속
2. **Create token** → 이름 예: `venom-apply`
3. **Scopes**: `data.records:write` 추가 (선택: `data.records:read`)
4. **Access**: 베이스 `베놈 체크업 — 진단 신청 접수` 선택
5. 생성된 토큰(`pat...`) 복사 — **한 번만 보이므로 메모**

### 2. Vercel 환경변수 등록
베놈 사이트가 배포된 Vercel 프로젝트에서:
1. **Settings → Environment Variables**
2. 아래 3개 추가 (Production·Preview 모두 체크)

| Name | Value |
|---|---|
| `AIRTABLE_TOKEN` | 위에서 복사한 `pat...` 토큰 |
| `AIRTABLE_BASE_ID` | `appvjDAassfO6Q39W` |
| `AIRTABLE_TABLE` | `진단 신청` |

3. **Redeploy** (환경변수는 재배포해야 적용됨)

### 3. 동작 확인
- 랜딩 페이지에서 필수 항목만 채우고 신청 → Airtable에 새 행이 뜨면 성공.
- 안 뜨면: Vercel 함수 로그에서 `apply` 확인, 토큰 scope·베이스 접근 권한 점검.

## 참고 / 주의

- **Vercel Hobby 요금제는 서버리스 함수 12개 제한**입니다. 현재 `api/` 폴더에 함수가 12개를 넘으면
  배포가 막힐 수 있으니, 그 경우 Pro 업그레이드 또는 기존 함수 통합이 필요합니다. (배포 로그에서 확인)
- CORS 허용 출처는 `apply.js`의 `ALLOW_ORIGINS`에 지정돼 있습니다. 허브·랜딩 도메인을
  커스텀 도메인으로 옮기면 이 목록에 추가하세요.
- 동의 증적(일시·문구버전·항목·IP)이 함께 저장되므로 정보통신망법·개인정보보호법 대응 근거가 됩니다.
- 광고성 정보(문자·메일·전화)는 **동의한 채널에만** 발송해야 하며, 2년 주기 재동의 확인이 필요합니다.
