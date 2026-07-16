"""3주차 데모 시나리오 시드 (MVP_개발계획.md 9장 Definition of Done)

서버가 켜진 상태에서 실행: python3 seed_demo.py
API를 통해 신고를 넣어 접수→분류→중복누적→검수큐 흐름을 재현한다.
"""
import io
import random

import requests
from PIL import Image

BASE = "http://127.0.0.1:8000"

# (설명, 위치, 유형, 같은 사진 반복 횟수) — 좌표는 geo.py 목업 기준
SCENARIOS = [
    ("어린이보호구역 포트홀 — 반복 신고로 우선순위 상승", (36.4791, 127.2581), "도로 파손", 6),
    ("주거지역 가로등 고장 — 세종여고 앞 사례", (36.4795, 127.2510), "가로등 고장", 3),
    ("외곽 쓰레기 무단투기 — 낮음 등급", (36.4400, 127.3400), "쓰레기 무단투기", 1),
    ("병원 인근 도로 파손", (36.4870, 127.2810), "도로 파손", 2),
    ("유형 미선택 신고 — AI 자동 분류 (저신뢰 시 검수 큐)", (36.5180, 127.2600), None, 1),
    ("기타 유형 수기 작성", (36.5050, 127.2470), "벤치 파손", 1),
]


def fake_photo(seed: int) -> bytes:
    random.seed(seed)
    img = Image.new("RGB", (320, 240),
                    (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def main():
    for i, (desc, (lat, lng), cat, repeat) in enumerate(SCENARIOS):
        photo = fake_photo(i)
        for n in range(repeat):
            data = {"lat": lat, "lng": lng, "consent": "true"}
            if cat:
                data["category_user"] = cat
            r = requests.post(f"{BASE}/api/complaints", data=data,
                              files={"photo": (f"demo{i}.jpg", photo, "image/jpeg")})
            r.raise_for_status()
        d = r.json()
        merged = f" (누적 {d.get('report_count', 1)}건)" if repeat > 1 else ""
        print(f"#{d['id']} {desc}{merged}")
        print(f"   분류={d.get('category')} 점수={d.get('priority_score')} "
              f"등급={d.get('priority_grade')} 검수사유={d.get('review_reason')}")
    print(f"\n관리자 화면: {BASE}/admin  ·  시민 신고 화면: {BASE}/")


if __name__ == "__main__":
    main()
