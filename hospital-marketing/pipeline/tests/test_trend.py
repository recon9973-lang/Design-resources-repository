"""월간 추이·측정기반 종합점수 단위 테스트: python3 -m unittest discover -s tests

신뢰도가 생명 — 조작값 금지·미측정 정직 표기 원칙을 회귀 테스트로 보호한다.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_diagnose as ad
from medirank import scoring


def _result(title, addr, month, composite, place_hit, content_hit, competitors):
    return {
        "generated_at": f"{month}-15T00:00:00+00:00",
        "business": {"title": title, "address": addr},
        "location": {"competitors": competitors},
        "composite": {"score": composite},
        "summary": {"place_hit": place_hit, "content_hit": content_hit, "total": 12},
    }


class TestTrend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        Path(self.path).unlink(missing_ok=True)

    def test_first_diagnosis_is_honest(self):
        # 이력이 없으면 조작 없이 first=True, deltas 비어 있어야 한다.
        t = ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-07", 60, 4, 5, 9), self.path)
        self.assertTrue(t["first"])
        self.assertIsNone(t["prior_month"])
        self.assertEqual(t["deltas"], {})

    def test_delta_against_prior_month(self):
        ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-06", 61, 4, 5, 9), self.path)
        t = ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-07", 67, 6, 5, 11), self.path)
        self.assertFalse(t["first"])
        self.assertEqual(t["prior_month"], "2026-06")
        self.assertEqual(t["deltas"],
                         {"composite": 6.0, "place_hit": 2, "content_hit": 0, "competitors": 2})

    def test_same_month_replaces_not_duplicates(self):
        ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-07", 60, 4, 5, 9), self.path)
        ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-07", 62, 5, 5, 9), self.path)
        hist = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["composite"], 62)

    def test_different_hospital_isolated(self):
        ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-06", 61, 4, 5, 9), self.path)
        t = ad.compute_trend(_result("새봄이비인후과", "서울 강남구 역삼동 5", "2026-07", 50, 2, 1, 3), self.path)
        # 다른 병원은 남의 이력과 비교하지 않는다.
        self.assertTrue(t["first"])

    def test_missing_measure_yields_no_delta(self):
        ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-06", None, 4, 5, 9), self.path)
        t = ad.compute_trend(_result("행복치과", "대구 수성구 범어동 1", "2026-07", 67, 6, 5, 11), self.path)
        # 이전 composite가 미측정(None)이면 조작값으로 채우지 않고 delta 생략.
        self.assertNotIn("composite", t["deltas"])
        self.assertEqual(t["deltas"]["place_hit"], 2)


class TestNoShadowing(unittest.TestCase):
    def test_main_does_not_shadow_module_cell(self):
        # 회귀: 벤치마크 루프가 모듈함수 cell()을 지역변수로 가리면
        # 진단 루프의 cell(ct[key]) 호출이 UnboundLocalError로 깨진다.
        import dis
        names = {i.argval for i in dis.get_instructions(ad.main)
                 if i.opname == "STORE_FAST"}
        self.assertNotIn("cell", names,
                         "main()이 'cell'을 지역변수로 재사용하면 모듈함수 cell()이 가려짐")
        self.assertTrue(callable(ad.cell))


class TestPlaceQualityPartial(unittest.TestCase):
    def test_none_when_nothing_measured(self):
        pq = scoring.place_quality_partial()
        self.assertIsNone(pq["score"])
        self.assertEqual(pq["frac"], 0.0)
        self.assertEqual(pq["measured"], [])

    def test_info_only_partial_frac(self):
        pq = scoring.place_quality_partial(info_completeness=1.0)
        self.assertEqual(pq["measured"], ["info"])
        self.assertEqual(pq["frac"], 0.25)          # info 가중만
        self.assertEqual(pq["score"], 100.0)        # 측정된 축 내 정규화

    def test_full_measure(self):
        pq = scoring.place_quality_partial(review_count=200, rating=5.0,
                                           photo_count=80, info_completeness=1.0)
        self.assertEqual(pq["frac"], 1.0)
        self.assertEqual(pq["score"], 100.0)


class TestCompositeMeasured(unittest.TestCase):
    def test_all_none_is_none(self):
        c = scoring.composite_measured()
        self.assertIsNone(c["score"])
        self.assertEqual(c["measured_weight_pct"], 0)

    def test_excludes_missing_axes_and_renormalizes(self):
        # 노출만 측정(100) → 다른 축 미측정이면 종합=100(측정 축만 재정규화).
        c = scoring.composite_measured(exposure=100.0)
        self.assertEqual(c["score"], 100.0)
        self.assertEqual(c["measured_weight_pct"], 40)   # 노출 가중 40%만 반영
        self.assertIsNone(c["components"]["density"])

    def test_place_frac_scales_weight(self):
        # 플레이스 부분측정(frac=0.25)이면 반영 가중이 그만큼만.
        c = scoring.composite_measured(exposure=0.0, place=100.0, place_frac=0.25)
        # 반영 가중 = 노출 40% + 플레이스 15%*0.25 = 43.75% → 반올림 44
        self.assertEqual(c["measured_weight_pct"], 44)


if __name__ == "__main__":
    unittest.main()
