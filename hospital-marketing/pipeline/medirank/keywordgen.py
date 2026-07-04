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
        if city is None and re.search(r"(특별시|광역시|특별자치시|특별자치도)$", tok):
            is_province = tok.endswith("특별자치도")  # 강원특별자치도 등
            city = re.sub(r"(특별시|광역시|특별자치시|특별자치도)$", "", tok)
        elif city is None and tok.endswith("도") and len(tok) <= 4:
            city = tok[:-1]  # 경기도→경기, (구)강원도→강원
            is_province = True
        elif city is None and tok.endswith("시"):
            city = tok[:-1]
        elif gu is None and is_province and tok.endswith("시") and len(tok) >= 2:
            gu = tok  # 도 아래의 시(춘천시 등) = 구 단위로 사용 → SGIS 시 기준 조회
        elif gu is None and re.search(r"[구군]$", tok) and len(tok) >= 2:
            gu = tok
        elif dong is None and re.search(r"[동읍면가]$", tok) and not tok[0].isdigit():
            dong = tok
            break
    return {"city": city, "gu": gu, "dong": dong}


def classify(category: str) -> dict:
    """네이버 지역검색 category → 업종 판별.

    반환: {is_hospital, dept(진료과|None), base_terms[기본 키워드들]}
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
        return {"is_hospital": True, "dept": dept, "base_terms": base}

    terms = INDUSTRY_TERMS.get(tail, [tail] if tail else [])
    return {"is_hospital": False, "dept": None, "base_terms": terms[:3]}


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


def generate_keywords(profile: dict, max_keywords: int = 12) -> list[dict]:
    """업체 프로필 → [{kw, type, base}] 목록.

    지역 축: 동 → 구 → 시 → 메인(지역 없음) 순으로 좁은 지역부터.
    기본 축: 업종/진료과 + 주요 시술 (base_terms).
    """
    region = parse_region(profile.get("address_jibun") or profile.get("address") or "")
    cls = classify(profile.get("category") or "")
    axes = [("동단위", region["dong"]), ("구단위", region["gu"]),
            ("시단위", region["city"]), ("메인", None)]

    out = [{"kw": profile["title"], "type": "브랜드", "base": profile["title"]}]
    seen = {_norm(profile["title"])}
    for base in cls["base_terms"]:
        if not base:
            continue
        for label, reg in axes:
            kw = f"{reg} {base}" if reg else base
            if _norm(kw) in seen:
                continue
            seen.add(_norm(kw))
            out.append({"kw": kw, "type": label, "base": base})
            if len(out) >= max_keywords:
                return out
    return out
