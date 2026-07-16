"""우선순위 엔진 단위 테스트 — MVP_개발계획.md 5장 '검증용 예시 계산 3건' 기대값"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from datetime import datetime, timedelta

from app.priority import priority, grade


NOW = datetime(2026, 7, 16, 12, 0)


def hours_ago(h):
    return NOW - timedelta(hours=h)


class TestPriorityExamples(unittest.TestCase):
    def test_example1_urgent(self):
        # ① 어린이보호구역 도로 파손, 6회, 80h 경과 → 40+25+20+15 = 100 → 긴급
        score, g = priority("도로 파손", 25, 6, hours_ago(80), NOW)
        self.assertEqual(score, 100)
        self.assertEqual(g, "긴급")

    def test_example2_high(self):
        # ② 주거지역 가로등 고장, 3회, 30h 경과 → 40+15+12+9 = 76 → 높음
        score, g = priority("가로등 고장", 15, 3, hours_ago(30), NOW)
        self.assertEqual(score, 76)
        self.assertEqual(g, "높음")

    def test_example3_low(self):
        # ③ 외곽 쓰레기 무단투기, 1회, 10h 경과 → 24+5+4+3 = 36 → 낮음
        score, g = priority("쓰레기 무단투기", 5, 1, hours_ago(10), NOW)
        self.assertEqual(score, 36)
        self.assertEqual(g, "낮음")

    def test_two_axis_ordering(self):
        # 정합성: 위험도x경과시간 순서가 높/長 > 높/短 > 낮/長 > 낮/短
        high_long, _ = priority("도로 파손", 5, 1, hours_ago(80), NOW)
        high_short, _ = priority("도로 파손", 5, 1, hours_ago(3), NOW)
        low_long, _ = priority("쓰레기 무단투기", 5, 1, hours_ago(80), NOW)
        low_short, _ = priority("쓰레기 무단투기", 5, 1, hours_ago(3), NOW)
        self.assertTrue(high_long > high_short > low_long > low_short)

    def test_grade_cutoffs(self):
        self.assertEqual(grade(85), "긴급")
        self.assertEqual(grade(84), "높음")
        self.assertEqual(grade(60), "높음")
        self.assertEqual(grade(59), "보통")
        self.assertEqual(grade(40), "보통")
        self.assertEqual(grade(39), "낮음")

    def test_etc_category_minor_risk(self):
        # 경미형(기타·확장 유형) 위험도 8
        score, _ = priority("불법주정차", 5, 1, hours_ago(3), NOW)
        self.assertEqual(score, 8 + 5 + 4 + 3)


if __name__ == "__main__":
    unittest.main()
