"""업체명만으로 진단 키워드를 자동 산출한다.

흐름:
1. resolve_business(업체명) — 네이버 지역검색으로 업체를 특정
   (동명 업체가 흔하므로 후보 전부를 반환하고, region_hint로 좁힐 수 있다)
2. generate_keywords(profile) — 주소에서 시/구/동을 뽑고, 업종(병원이면
   진료과목·주요 시술)을 판별해 [동/구/시/메인] × [업종·시술] 조합을 만든다.

공식 API만 사용한다. 연관검색어·자동완성 화면 스크래핑은 쓰지 않는다.
절대 검색량·연관검색어는 검색광고 키워드도구 API(광고주 계정)로 합법 취득하며,
connectors/searchad.py + auto_diagnose가 키 연동 시 리포트에 자동 반영한다.
"""

import re

from .connectors import naver_local

# 병원 진료과 → 환자들이 실제로 검색하는 주요 진료·시술 키워드 (당사 사전)
DEPT_PROCS = {
    "피부과": ["여드름치료", "기미레이저", "리프팅"],
    "치과": ["임플란트", "치아교정", "스케일링"],
    "한의원": ["추나요법", "다이어트한약", "교통사고한의원"],
    "정형외과": ["도수치료", "무릎통증", "어깨통증"],
    "성형외과": ["쌍꺼풀수술", "코성형", "리프팅"],
    "안과": ["스마일라식", "라섹", "백내장수술"],
    "이비인후과": ["비염치료", "코골이", "어지럼증"],
    "내과": ["위내시경", "대장내시경", "건강검진"],
    "산부인과": ["여성검진", "산전검사", "자궁경부암검사"],
    "비뇨의학과": ["전립선검사", "요로결석", "방광염"],
    "소아청소년과": ["영유아검진", "예방접종", "성장클리닉"],
    "정신건강의학과": ["우울증상담", "불면증", "공황장애"],
    "동물병원": ["강아지건강검진", "고양이병원", "중성화수술"],
}

# 진료과 → 환자가 '증상·고민'으로 검색하는 말 (정보탐색 단계, 기획서 keyword_type=symptom)
# 시술명이 아니라 환자 언어의 증상/고민. 치료효과·보장 표현은 넣지 않는다.
DEPT_SYMPTOMS = {
    "피부과": ["여드름", "기미", "모공", "홍조"],
    "치과": ["치아통증", "잇몸부음", "사랑니"],
    "한의원": ["교통사고후유증", "허리통증", "소화불량"],
    "정형외과": ["무릎통증", "오십견", "허리디스크", "목디스크"],
    "성형외과": ["눈처짐", "코막힘성형", "안면비대칭"],
    "안과": ["시력저하", "안구건조", "비문증"],
    "이비인후과": ["코막힘", "이명", "목이물감"],
    "내과": ["소화불량", "만성피로", "복통"],
    "산부인과": ["생리불순", "질염", "갱년기"],
    "비뇨의학과": ["빈뇨", "혈뇨", "잔뇨감"],
    "소아청소년과": ["아기열", "수족구", "장염"],
    "정신건강의학과": ["불면", "불안", "우울감"],
}

# 진료과 → HIRA 진료과목코드 (입지 경쟁 분석용, 동물병원은 HIRA 미수록)
DEPT_DGSBJT = {
    "내과": "01", "정신건강의학과": "03", "정형외과": "05", "성형외과": "08",
    "산부인과": "10", "소아청소년과": "11", "안과": "12", "이비인후과": "13",
    "피부과": "14", "비뇨의학과": "15", "치과": "49", "한의원": "80",
}

# 비병원 업종(카테고리 말단) → 관련 키워드 (당사 사전, 없으면 말단 그대로)
INDUSTRY_TERMS = {
    "광고대행": ["마케팅", "병원마케팅", "광고대행사"],
    "미용실": ["미용실", "헤어살롱"],
    "네일아트": ["네일샵", "네일아트"],
    "피부관리": ["피부관리", "에스테틱"],
    "학원": ["학원", "과외"],
    "부동산중개": ["부동산", "공인중개사"],
    "변호사": ["변호사", "법률상담"],
    "세무사": ["세무사", "기장대행"],
    "소프트웨어개발": ["앱개발", "웹개발"],
    "카페": ["카페", "디저트카페"],
    "헬스클럽": ["헬스장", "PT"],
    "필라테스": ["필라테스", "요가"],
}

# 도(道) 정식명 → 사람들이 실제로 쓰는 축약형. 지번주소 앞머리 토큰과 정확히 일치할 때만 적용.
# '경상북도'를 마지막 글자만 떼어 '경상북'으로 만드는 오류를 막는다(→ '경북').
PROVINCE_ABBR = {
    "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원",
    "충청북도": "충북", "충청남도": "충남",
    "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남",
    "제주도": "제주", "제주특별자치도": "제주",
}

# 상호(업체명)에 드러나는 전문분야 신호 → (진료과, 대표 검색어들).
# 네이버 카테고리가 '병원부속시설' 등으로 불명확하거나 진료과 판별에 실패했을 때,
# 상호에서 환자가 실제로 검색하는 말을 보완한다. 앞의 패턴이 우선.
# 의료광고 유의: 면허 진료과가 불확실한 상호형 클리닉은 진료과명 대신 일반 탐색어를 대표어로 둔다.
NAME_SPECIALTY = [
    ("스킨", "피부과", ["피부관리", "스킨케어", "피부"]),
    ("더마", "피부과", ["피부관리", "피부", "스킨케어"]),
    ("피부", "피부과", ["피부과", "피부관리", "피부"]),
    ("탈모", "피부과", ["탈모치료", "탈모"]),
    ("성형", "성형외과", ["성형외과", "쌍꺼풀", "코성형"]),
    ("치과", "치과", ["치과", "임플란트", "치아교정"]),
    ("한의원", "한의원", ["한의원", "추나요법", "한약"]),
    ("한방", "한의원", ["한의원", "한방치료"]),
    ("재활", "정형외과", ["재활치료", "도수치료"]),
    ("통증", "정형외과", ["통증의학과", "도수치료", "허리통증"]),
    ("정형", "정형외과", ["정형외과", "도수치료", "무릎통증"]),
    ("안과", "안과", ["안과", "라식", "백내장"]),
    ("이비인후", "이비인후과", ["이비인후과", "비염치료"]),
    ("산부인", "산부인과", ["산부인과", "여성검진"]),
    ("비뇨", "비뇨의학과", ["비뇨의학과", "전립선검사"]),
    ("소아", "소아청소년과", ["소아청소년과", "예방접종"]),
    ("정신건강", "정신건강의학과", ["정신건강의학과", "심리상담"]),
]

_NORM_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _NORM_WS.sub("", s or "")


def parse_region(jibun_address: str) -> dict:
    """지번 주소 → {city, gu, dong}. 예: '대구광역시 수성구 두산동 123'.

    도(道)·특별자치도의 경우 그 아래 '○○시'를 구 단위(중간 지역)로 잡는다.
    예: '강원특별자치도 춘천시 중앙로2가' → city=강원, gu=춘천시, dong=중앙로2가.
    이렇게 해야 SGIS 인구·상권 지표가 도 전체가 아닌 해당 시 기준으로 계산된다.
    """
    city = gu = dong = None
    is_province = False
    for tok in (jibun_address or "").split():
        if city is None and tok in PROVINCE_ABBR:
            city = PROVINCE_ABBR[tok]  # 경상북도→경북, 강원특별자치도→강원 등(정식 축약)
            is_province = True
        elif city is None and re.search(r"(특별시|광역시|특별자치시)$", tok):
            city = re.sub(r"(특별시|광역시|특별자치시)$", "", tok)
        elif city is None and tok.endswith("도") and len(tok) <= 4:
            city = tok[:-1]  # 표에 없는 예비 '○○도'
            is_province = True
        elif city is None and tok.endswith("시"):
            city = tok[:-1]
        elif gu is None and is_province and tok.endswith("시") and len(tok) >= 2:
            gu = tok  # 도 아래의 시(포항시 등) = 구 단위로 사용 → SGIS 시 기준 조회
        elif gu is None and re.search(r"[구군]$", tok) and len(tok) >= 2:
            gu = tok
        elif dong is None and re.search(r"[동읍면가]$", tok) and not tok[0].isdigit():
            dong = tok
            break
    return {"city": city, "gu": gu, "dong": dong, "city_is_province": is_province}


def classify(category: str, title: str = "") -> dict:
    """네이버 지역검색 category(+상호) → 업종 판별.

    반환: {is_hospital, dept(진료과|None), base_terms[기본 키워드들], name_inferred?}
    카테고리가 '병원부속시설'처럼 불명확하면 상호(title)에서 전문분야를 보완한다.
    """
    cat = category or ""
    parts = [p.strip() for p in cat.split(">") if p.strip()]
    tail = parts[-1] if parts else ""

    hospital = ("병원" in cat) or ("의원" in cat) or tail in DEPT_PROCS
    if hospital:
        dept = tail if tail in DEPT_PROCS else None
        if dept is None:  # "병원,의원>내과/외과" 같은 복합 표기 대응
            for d in DEPT_PROCS:
                if d in cat:
                    dept = d
                    break
        base = [dept] if dept else ["병원"]
        base += DEPT_PROCS.get(dept, [])[:2]
        cls = {"is_hospital": True, "dept": dept, "base_terms": base}
        # 진료과 판별 실패 또는 생성적 '병원'뿐이면 상호에서 전문분야를 보완한다.
        if dept is None or base == ["병원"]:
            hit = _infer_from_name(title)
            if hit:
                cls.update(dept=hit[0], base_terms=list(hit[1]), name_inferred=True)
        return cls

    terms = INDUSTRY_TERMS.get(tail, [tail] if tail else [])
    return {"is_hospital": False, "dept": None, "base_terms": terms[:3]}


def _infer_from_name(title: str):
    """상호에서 전문분야 신호를 찾아 (진료과, [대표 검색어…]) 반환. 없으면 None."""
    norm = _norm(title)
    for pat, dept, terms in NAME_SPECIALTY:
        if pat in norm:
            return dept, terms
    return None


def resolve_business(name: str, region_hint: str | None = None) -> dict:
    """업체명(+선택 지역 힌트)으로 업체를 특정한다.

    반환: {chosen(dict|None), candidates(list), note}
    선정 순서: (지역 힌트 일치) > 상호 완전일치 > 상호 포함 > 첫 결과
    """
    queries = [name]
    if region_hint:
        queries.insert(0, f"{region_hint} {name}")

    seen, candidates = set(), []
    for q in queries:
        for r in naver_local.search_local(q):
            key = (r["title"], r.get("address_jibun") or r["address"])
            if key not in seen:
                seen.add(key)
                candidates.append(r)

    norm_n = _norm(name)

    def score(r):
        t = _norm(r["title"])
        s = 0
        if region_hint and region_hint in ((r.get("address_jibun") or "") + (r["address"] or "")):
            s += 4
        if t == norm_n:
            s += 3
        elif t.startswith(norm_n) or t.endswith(norm_n):
            s += 2
        elif norm_n in t:
            s += 1
        return s

    if not candidates:
        return {"chosen": None, "candidates": [], "note": "지역검색 결과 없음"}
    chosen = max(candidates, key=score)
    note = ""
    same_name = [c for c in candidates if _norm(name) in _norm(c["title"])]
    if len(same_name) > 1 and not region_hint:
        note = f"동명·유사 상호 {len(same_name)}곳 — 지역 힌트(--region)로 좁히면 정확합니다"
    return {"chosen": chosen, "candidates": candidates, "note": note}


def generate_keywords(profile: dict, max_keywords: int = 14) -> list[dict]:
    """업체 프로필 → [{kw, type, base}] 목록.

    커버 축(기획서 keyword_type 대응):
    - 브랜드 / 진료과·시술 × 지역(동→구→시→메인)
    - 증상·고민(정보탐색) · 비교탐색형(추천) — 병원 대표 지역 스코프
    좁은 지역·정보탐색 초기 단계까지 포괄해 환자 검색 여정 전체를 진단한다.
    """
    region = parse_region(profile.get("address_jibun") or profile.get("address") or "")
    cls = classify(profile.get("category") or "", profile.get("title") or "")
    gu, city, dong = region["gu"], region["city"], region["dong"]
    if region.get("city_is_province"):
        # 도(道) 지역: '경북·강원' 같은 도명은 검색어로 쓰지 않는다(아무도 그렇게 안 찾음).
        # 생활권 검색 단위는 그 아래 '시'(예: 포항). gu에 '포항시'가 담겨 있으니
        # 접미사 '시'를 떼어 '포항'을 대표 지역으로 쓴다.
        si = re.sub(r"시$", "", gu) if gu else None
        axes = [("동단위", dong), ("시단위", si or gu), ("메인", None)]
        reg_main = si or gu or dong
    else:
        # 특별시·광역시: 동 < 구 < 시(광역) 모두 실제 검색 단위
        axes = [("동단위", dong), ("구단위", gu), ("시단위", city), ("메인", None)]
        reg_main = gu or city  # 대표 지역(증상·비교 키워드 스코프)

    out = [{"kw": profile["title"], "type": "브랜드", "base": profile["title"]}]
    seen = {_norm(profile["title"])}

    def add(kw: str, typ: str, base: str) -> bool:
        if not kw or _norm(kw) in seen:
            return True
        seen.add(_norm(kw))
        out.append({"kw": kw, "type": typ, "base": base})
        return len(out) < max_keywords

    base_terms = [b for b in cls["base_terms"] if b]
    dept_term = base_terms[0] if base_terms else None
    procs = base_terms[1:]

    # 1) 대표 진료과/업종 × 지역축 (동→구→시→메인)
    if dept_term:
        for label, reg in axes:
            if not add(f"{reg} {dept_term}" if reg else dept_term, label, dept_term):
                return out
    # 2) 증상·고민(정보탐색) — 병원만, 대표 지역
    if cls["is_hospital"]:
        for sym in DEPT_SYMPTOMS.get(cls["dept"] or "", [])[:3]:
            if not add(f"{reg_main} {sym}" if reg_main else sym, "증상", sym):
                return out
    # 3) 비교 탐색형 — 대표 지역 + 진료과 + 추천 (중립 검색 패턴)
    if dept_term and reg_main:
        if not add(f"{reg_main} {dept_term} 추천", "비교", dept_term):
            return out
    # 4) 주요 시술 × 지역축 (남는 예산 채움)
    for base in procs:
        for label, reg in axes:
            if not add(f"{reg} {base}" if reg else base, label, base):
                return out
    return out
