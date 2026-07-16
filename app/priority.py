"""우선순위 엔진 (MVP_개발계획.md 5장)

점수(100) = w1(40)·유형 위험도 + w2(25)·위치 민감도 + w3(20)·신고 빈도 + w4(15)·경과 시간
등급 컷오프: 긴급 >=85 / 높음 60~84 / 보통 40~59 / 낮음 <40
"""
from datetime import datetime

# w1: 유형 위험도 — 안전위험형 40 / 환경저해형 24 / 경미형(기타·확장 유형) 8
TYPE_RISK = {
    "도로 파손": 40,
    "가로등 고장": 40,
    "쓰레기 무단투기": 24,
}
TYPE_RISK_DEFAULT = 8


def type_risk_score(category: str) -> int:
    return TYPE_RISK.get(category, TYPE_RISK_DEFAULT)


def frequency_score(report_count: int) -> int:
    # w3: 5회 이상 20 / 2~4회 12 / 1회 4
    if report_count >= 5:
        return 20
    if report_count >= 2:
        return 12
    return 4


def elapsed_score(reported_at: datetime, now: datetime | None = None) -> int:
    # w4: 72시간 이상 15 / 24~72시간 9 / 24시간 이내 3
    now = now or datetime.now()
    hours = (now - reported_at).total_seconds() / 3600
    if hours >= 72:
        return 15
    if hours >= 24:
        return 9
    return 3


def grade(score: int) -> str:
    if score >= 85:
        return "긴급"
    if score >= 60:
        return "높음"
    if score >= 40:
        return "보통"
    return "낮음"


def priority(category: str, location_score: int, report_count: int,
             reported_at: datetime, now: datetime | None = None) -> tuple[int, str]:
    """(점수, 등급) 반환. location_score는 geo.location_sensitivity() 결과(25/15/5)."""
    score = (type_risk_score(category) + location_score
             + frequency_score(report_count) + elapsed_score(reported_at, now))
    return score, grade(score)
