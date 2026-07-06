"""리포트 렌더 구조 회귀 테스트: python3 -m unittest discover -s tests

- 영역별 노출 정보 중복 금지(레이더 옆 '영역별 노출 키워드 수' 막대 제거)
- 상위 5 노출 현황은 대표 키워드만(병원명=브랜드 제외 — 전국 동명 지점 오염 방지)
- JSON 재로드 렌더(반경 키가 str)에서도 깨지지 않아야 함
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_diagnose as ad

DATA = Path(__file__).resolve().parent.parent.parent / "data" / "auto-siwon-pain.json"


@unittest.skipUnless(DATA.exists(), "샘플 진단 JSON 없음")
class TestReportStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.j = json.loads(DATA.read_text(encoding="utf-8"))
        cls.html = ad.render_html(cls.j)

    def test_renders_from_reloaded_json(self):
        # 반경 breakdown 키가 str이어도 렌더가 죽지 않는다(회귀).
        from html.parser import HTMLParser
        HTMLParser().feed(self.html)
        self.assertIn("<title>", self.html)

    def test_no_duplicate_area_bars(self):
        # 레이더 옆 중복 막대('영역별 노출 키워드 수')는 제거됐다.
        self.assertNotIn("영역별 노출 키워드 수", self.html)
        # 대체 패널이 자리한다.
        self.assertIn("지금 안 보이는 키워드", self.html)

    def test_top5_excludes_brand_keyword(self):
        brand = self.j["business"]["title"]
        summaries = re.findall(r"<summary>(.*?)</summary>", self.html)
        self.assertTrue(summaries, "상위5 키워드 행이 없음")
        # 브랜드(병원명) 키워드는 상위5 드릴다운에서 제외된다.
        self.assertNotIn(brand, [re.sub(r"<.*?>", "", s).strip() for s in summaries])

    def test_medical_ad_safe_legend_present(self):
        # '의료광고 안전' 태그의 의미(의료법 제56조) 설명이 리포트에 포함된다.
        self.assertIn("의료법 제56조", self.html)

    def test_logo_and_no_auto_branding(self):
        # (주)베놈 로고(인라인 SVG V마크)가 텍스트 대신 사용된다.
        self.assertIn('class="vmark"', self.html)
        self.assertIn("VENOMAD", self.html)
        # 제목·헤더에서 '자동' 브랜딩 제거(신뢰도 — 자동생성물처럼 보이지 않게).
        self.assertNotIn("자동 진단 리포트", self.html)
        self.assertIn("진단 리포트", self.html)


if __name__ == "__main__":
    unittest.main()
