"""3주차 데모 시나리오 시드 (MVP_개발계획.md 9장 Definition of Done)

서버가 켜진 상태에서 실행: python3 seed_demo.py
API를 통해 신고를 넣어 접수→분류→중복누적→검수큐 흐름을 재현한다.

사진은 seed_images/ 에 커밋된 실제 도로파손·가로등·쓰레기 목업 사진을 사용한다
(원본 정리 폴더: 여름방학 인턴쉽/mockup — 팀원 전원 클론 시에도 동일하게 동작하도록
저장소 안에 큐레이션된 일부만 복사해 넣었다). classify()는 이미지 내용이 아니라
바이트 해시로 분류하는 목업이라, 아래 각 파일의 top1_prob/margin은 미리 계산해
의도한 흐름(자동 확정 vs LOW_CONF 검수 큐)이 실제로 재현되는 파일만 골랐다.
"""
import os

import requests

BASE = "http://127.0.0.1:8000"
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_images")

# (설명, 위치, 유형, 사진 경로, 같은 사진 반복 횟수) — 좌표는 geo.py 목업 기준
SCENARIOS = [
    ("어린이보호구역 포트홀 — 반복 신고로 우선순위 상승", (36.4791, 127.2581),
     "도로 파손", "도로파손/pothole_02.png", 6),
    ("주거지역 가로등 고장 — 세종여고 앞 사례", (36.4795, 127.2510),
     "가로등 고장", "가로등/streetlight_01.png", 3),
    ("외곽 쓰레기 무단투기 — 낮음 등급", (36.4400, 127.3400),
     "쓰레기 무단투기", "쓰레기/illegal_dumping_03.png", 1),
    ("병원 인근 도로 파손", (36.4870, 127.2810),
     "도로 파손", "도로파손/public_property_damage_03.png", 2),
    ("유형 미선택 신고 — AI 자동 분류, 확신도 낮아 검수 큐로 이관", (36.5180, 127.2600),
     None, "도로파손/pothole_04_low_confidence.png", 1),
    ("기타 유형 수기 작성", (36.5050, 127.2470),
     "벤치 파손", "도로파손/public_property_damage_05.png", 1),
]


def load_photo(rel_path: str) -> bytes:
    with open(os.path.join(SEED_DIR, rel_path), "rb") as f:
        return f.read()


def main():
    for i, (desc, (lat, lng), cat, photo_path, repeat) in enumerate(SCENARIOS):
        photo = load_photo(photo_path)
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
