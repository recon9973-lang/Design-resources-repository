# -*- coding: utf-8 -*-
"""McKinsey 스타일로 12개 덱 전체 생성 — 기존 콘텐츠 재사용, 시각 레이어만 교체."""
import os
import mck_helpers as H
from build_student import DECKS as SDECKS, COURSE
from build_instructor import DECKS as IDECKS, expand_demo_tables

OUT_S = os.environ.get("OUT_S", "./수강생용")
OUT_I = os.environ.get("OUT_I", "./강사용")
CR = "Copyright · AI 시대 마케팅 6주 실무 스터디"


def theme(num, topic, instructor=False):
    return {
        "kicker": f"{num}회차 · {topic}" + ("  ·  강사 가이드" if instructor else ""),
        "tracker": f"{num}  {topic}",
        "copyright": CR,
        "source": None,
        "badge": "강사용" if instructor else None,
    }


if __name__ == "__main__":
    os.makedirs(OUT_S, exist_ok=True)
    os.makedirs(OUT_I, exist_ok=True)
    for num, topic, slides in SDECKS:
        th = theme(num, topic, instructor=False)
        fname = f"{num}회차_{topic.replace(' ', '_')}.pptx"
        n = H.build_deck(os.path.join(OUT_S, fname), th, slides)
        print(f"OK  수강생용/{fname}  ({n} slides)")
    for num, topic, slides in IDECKS:
        th = theme(num, topic, instructor=True)
        slides = expand_demo_tables(slides)
        fname = f"{num}회차_강사용_미션_시연가이드.pptx"
        n = H.build_deck(os.path.join(OUT_I, fname), th, slides)
        print(f"OK  강사용/{fname}  ({n} slides)")
