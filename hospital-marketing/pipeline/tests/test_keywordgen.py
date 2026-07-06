"""키워드 추출 근거 단위 테스트: python3 -m unittest discover -s tests

신뢰도가 생명 — 지역 축약·상호기반 전문분야 보완이 '실제 검색되는' 키워드만
만들도록 회귀 테스트로 고정한다. (예: '경상북 정형외과' 같은 유령 키워드 금지)
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from medirank import keywordgen as kg


class TestProvinceAbbrev(unittest.TestCase):
    def test_four_char_provinces(self):
        # 마지막 글자만 떼면 '경상북'이 되는 오류 — 정식 축약으로 교정돼야 한다.
        cases = {
            "경상북도 포항시 북구 죽도동": "경북",
            "경상남도 창원시 성산구 상남동": "경남",
            "전라남도 여수시 학동": "전남",
            "전라북도 전주시 완산구": "전북",
            "충청북도 청주시 흥덕구": "충북",
            "충청남도 천안시 서북구": "충남",
        }
        for addr, exp in cases.items():
            self.assertEqual(kg.parse_region(addr)["city"], exp, addr)

    def test_special_and_metro(self):
        self.assertEqual(kg.parse_region("강원특별자치도 춘천시 중앙로")["city"], "강원")
        self.assertEqual(kg.parse_region("제주특별자치도 제주시 노형동")["city"], "제주")
        self.assertEqual(kg.parse_region("대구광역시 수성구 두산동")["city"], "대구")
        self.assertEqual(kg.parse_region("서울특별시 종로구 숭인동")["city"], "서울")

    def test_province_flag(self):
        self.assertTrue(kg.parse_region("경상북도 포항시 죽도동")["city_is_province"])
        self.assertFalse(kg.parse_region("대구광역시 수성구 두산동")["city_is_province"])


class TestNoGhostKeywords(unittest.TestCase):
    def test_province_level_keyword_not_emitted(self):
        # '경북/경상북 병원' 같은 도(道)단위 키워드는 절대 나오면 안 된다(아무도 안 찾음).
        prof = {"title": "행복정형외과의원", "category": "병원,의원>정형외과",
                "address_jibun": "경상북도 포항시 북구 죽도동 55"}
        kws = [k["kw"] for k in kg.generate_keywords(prof, 12)]
        for k in kws:
            self.assertNotIn("경상북", k, k)
            self.assertNotIn("경북 ", k, k)
        # 대신 생활권 '포항'이 대표 지역으로 쓰여야 한다.
        self.assertTrue(any(k.startswith("포항 ") for k in kws), kws)

    def test_metro_keeps_gu_and_city(self):
        prof = {"title": "행복정형외과의원", "category": "병원,의원>정형외과",
                "address_jibun": "대구광역시 수성구 두산동 55"}
        kws = [k["kw"] for k in kg.generate_keywords(prof, 12)]
        self.assertTrue(any("수성구" in k for k in kws), kws)
        self.assertTrue(any(k.startswith("대구 ") for k in kws), kws)


class TestNameSpecialtyInference(unittest.TestCase):
    def test_skin_clinic_gets_skin_terms(self):
        # 카테고리가 '병원부속시설'로 불명확 → 상호 '스킨'에서 피부 검색어 보완.
        prof = {"title": "시원스킨클리닉", "category": "병원>병원부속시설",
                "address_jibun": "경상북도 포항시 북구 죽도동 55"}
        cls = kg.classify(prof["category"], prof["title"])
        self.assertEqual(cls["dept"], "피부과")
        self.assertTrue(cls.get("name_inferred"))
        kws = [k["kw"] for k in kg.generate_keywords(prof, 12)]
        joined = " ".join(kws)
        # 스킨/피부 관련 검색어가 실제로 포함돼야 한다(생성적 '병원'만 나오면 실패).
        self.assertTrue(any(t in joined for t in ("피부관리", "스킨케어", "피부")), kws)
        self.assertNotIn("죽도동 병원", kws)

    def test_informative_category_not_overridden(self):
        # 카테고리로 진료과가 확실하면 상호 보완을 하지 않는다.
        prof = {"title": "시원마취통증의학과의원", "category": "병원,의원>정형외과",
                "address_jibun": "경상북도 포항시 북구 죽도동 55"}
        cls = kg.classify(prof["category"], prof["title"])
        self.assertEqual(cls["dept"], "정형외과")
        self.assertFalse(cls.get("name_inferred"))

    def test_generic_hospital_without_name_signal_stays_generic(self):
        prof = {"title": "행복의원", "category": "병원>병원부속시설",
                "address_jibun": "서울특별시 종로구 숭인동 1"}
        cls = kg.classify(prof["category"], prof["title"])
        self.assertIsNone(cls["dept"])
        self.assertEqual(cls["base_terms"], ["병원"])


if __name__ == "__main__":
    unittest.main()
