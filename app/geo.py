"""행정동·위치 민감도 목업 매핑 (지도·행정구역 API 실연동은 로드맵 — 계획서 1장 제외 항목)

좌표는 세종시 신도심 인근의 대략값으로, 데모 시나리오용 목업이다.
"""
import math

# 행정동 목업 centroid (세종특별자치시)
ADMIN_DONGS = [
    ("한솔동", 36.4801, 127.2571),
    ("새롬동", 36.4787, 127.2503),
    ("도담동", 36.5183, 127.2607),
    ("아름동", 36.5109, 127.2469),
    ("종촌동", 36.5049, 127.2465),
    ("보람동", 36.4629, 127.2857),
    ("소담동", 36.4666, 127.2946),
]

# 위치 민감도 목업 구역: (이름, lat, lng, 반경 m, 점수)
# w2: 어린이보호구역·병원 인근 25 / 주거밀집·상업지역 15 / 그 외 5
SENSITIVE_ZONES = [
    ("새뜸초등학교 어린이보호구역", 36.4790, 127.2580, 300, 25),
    ("도담초등학교 어린이보호구역", 36.5190, 127.2590, 300, 25),
    ("세종충남대학교병원 인근", 36.4869, 127.2807, 300, 25),
]
RESIDENTIAL_RADIUS_M = 1200  # 행정동 centroid 기준 주거밀집 간주 반경


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def to_admin_dong(lat: float, lng: float) -> str:
    """가장 가까운 목업 centroid의 행정동 반환."""
    return min(ADMIN_DONGS, key=lambda d: haversine_m(lat, lng, d[1], d[2]))[0]


def location_sensitivity(lat: float, lng: float) -> tuple[int, str]:
    """(점수, 판정 근거) 반환."""
    for name, zlat, zlng, radius, score in SENSITIVE_ZONES:
        if haversine_m(lat, lng, zlat, zlng) <= radius:
            return score, name
    for name, dlat, dlng in ADMIN_DONGS:
        if haversine_m(lat, lng, dlat, dlng) <= RESIDENTIAL_RADIUS_M:
            return 15, f"{name} 주거밀집"
    return 5, "그 외 지역"
