"""점수 엔진 단위 테스트: python3 -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from medirank import scoring
from medirank.geo import haversine_m, within_radius


class TestExposure(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(scoring.exposure_score([]), 0.0)

    def test_grades(self):
        results = [
            {"exposed": True, "rank": 1},   # 100
            {"exposed": True, "rank": 4},   # 75
            {"exposed": True, "rank": None},  # 55
            {"exposed": False, "rank": None},  # 0
        ]
        self.assertEqual(scoring.exposure_score(results), 57.5)

    def test_all_top(self):
        results = [{"exposed": True, "rank": r} for r in (1, 2, 3)]
        self.assertEqual(scoring.exposure_score(results), 100.0)


class TestDensity(unittest.TestCase):
    def test_no_competitors_is_100(self):
        self.assertEqual(scoring.density_score([], 1000), 100.0)

    def test_more_competitors_lower_score(self):
        near = [{"distance_m": 100, "same_department": True}] * 3
        many = near * 5
        s_few = scoring.density_score(near, 1000)
        s_many = scoring.density_score(many, 1000)
        self.assertGreater(s_few, s_many)

    def test_closer_competitor_weighs_more(self):
        w_near = scoring.competition_weight(100, True, 1000)
        w_far = scoring.competition_weight(900, True, 1000)
        self.assertGreater(w_near, w_far)

    def test_similar_department_weighs_less(self):
        w_same = scoring.competition_weight(300, True, 1000)
        w_sim = scoring.competition_weight(300, False, 1000)
        self.assertGreater(w_same, w_sim)


class TestDemandAndPlace(unittest.TestCase):
    def test_demand_bounds(self):
        self.assertEqual(scoring.demand_score(0, 0.5, 1000), 0.0)
        self.assertLessEqual(scoring.demand_score(10_000_000, 1.0, 500), 100.0)

    def test_place_quality_range(self):
        low = scoring.place_quality_score(0, 3.0, 0, 0.0)
        high = scoring.place_quality_score(500, 5.0, 300, 1.0)
        self.assertEqual(low, 0.0)
        self.assertEqual(high, 100.0)


class TestFinal(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(scoring.WEIGHTS.values()), 1.0)

    def test_final_is_weighted_sum(self):
        self.assertEqual(scoring.final_score(100, 100, 100, 100), 100.0)
        self.assertEqual(scoring.final_score(100, 0, 0, 0), 40.0)
        self.assertEqual(scoring.final_score(0, 100, 0, 0), 25.0)
        self.assertEqual(scoring.final_score(0, 0, 100, 0), 20.0)
        self.assertEqual(scoring.final_score(0, 0, 0, 100), 15.0)


class TestGeo(unittest.TestCase):
    def test_haversine_known_distance(self):
        # 강남역(37.4979,127.0276) ~ 역삼역(37.5006,127.0364) ≈ 830m
        d = haversine_m(37.4979, 127.0276, 37.5006, 127.0364)
        self.assertTrue(700 < d < 950, d)

    def test_within_radius_excludes_self_and_far(self):
        base = {"latitude": 37.5, "longitude": 127.03}
        cands = [
            {"name": "self", "latitude": 37.5, "longitude": 127.03},
            {"name": "near", "latitude": 37.503, "longitude": 127.03},
            {"name": "far", "latitude": 37.6, "longitude": 127.03},
        ]
        got = [c["name"] for c in within_radius(base, cands, 1000)]
        self.assertEqual(got, ["near"])


if __name__ == "__main__":
    unittest.main()
