# AI 영상·신고 기반 생활 인프라 유지관리 플랫폼 — MVP

`MVP_개발계획.md` 기반 구현. 시민 사진/GPS 신고 → 3개 유형 분류 → 우선순위 산정 → 담당 배정 → 검수·재분류의 전체 사이클이 동작한다.

## 실행

```bash
cd mvp
pip install -r requirements.txt
cp .env.example .env           # GEMINI_API_KEY=발급받은 키 를 채워넣는다 (없어도 목업으로 동작)
python3 -m app.server          # http://127.0.0.1:8000 에서 실행
```

계획서의 기술 스택 제안은 FastAPI였으나, 데모 환경에 이미 설치된 Flask로 대체했다(API 계층 차이일 뿐 로직 동일).

### AI 분류 · 재학습 동작 방식

`app/classifier.py`의 분류 우선순위는 다음과 같다.

1. `model/finetuned.pt`가 있으면 그 모델 사용 (담당자 화면의 "재학습 시작"으로 실제 생성됨)
2. 없고 `.env`에 `GEMINI_API_KEY`가 있으면 Gemini Vision API(`gemini-2.0-flash`)로 실시간 분류
3. 둘 다 없으면(키 미설정·API 오류) 결정적 목업 분류기로 폴백 — 키 없이도 앱 전체 흐름은 그대로 동작한다.

Gemini API 키는 [Google AI Studio](https://aistudio.google.com/apikey)에서 구글 로그인 후 무료로 발급받을 수 있다.

담당자 화면의 "AI 재학습 센터"는 더 이상 시뮬레이션이 아니다. `seed_images/`의 시드 사진과
담당자 검수(O/X)로 확정된 신고 사진을 모아 ImageNet 사전학습 MobileNetV3-Small 위에 새
분류 헤드를 실제로 학습시키고, 학습이 끝나면 `model/finetuned.pt`로 저장해 다음 분류부터
자동으로 적용한다(`app/retrain.py`). 화면에 보이는 진행률·loss·정확도는 매 epoch 검증셋에서
직접 측정한 실측치다.

| 화면 | URL |
|---|---|
| ① 시민 신고 (모바일 웹) | http://127.0.0.1:8000/ |
| ② 담당자 관리 | http://127.0.0.1:8000/admin |

데모 데이터 주입(서버 켠 상태에서):

```bash
python3 seed_demo.py
```

단위 테스트(계획서 5장 검증용 예시 3건 + 2축 정합성):

```bash
python3 -m unittest discover tests -v
```

## 구조

```
app/
  server.py      # Flask API + 화면 서빙 (접수/조회/검수/상태변경/이력/지표)
  classifier.py  # AI 분류 — 파인튜닝 모델 있으면 로드, 없으면 Gemini API, 둘 다 없으면 목업
  retrain.py     # AI 재학습 — seed_images+검수 데이터로 실제 파인튜닝 후 모델 저장/배포
  priority.py    # 우선순위 엔진 (w1~w4 가중치, 4등급)
  geo.py         # 행정동·위치 민감도 목업 매핑 (실 API 연동은 로드맵)
  db.py          # SQLite 스키마 (complaints / dept_mapping / audit_log)
static/
  report.html    # 시민 신고 화면 — 사진, GPS, 동의, 유형 선택, 상태 조회
  admin.html     # 담당자 화면 — 우선순위순 리스트, O/X 검수, 재분류, 상태 변경, 이력
tests/test_priority.py
seed_demo.py     # 3주차 데모 시나리오(DoD) 재현용 시드
```

## 계획서 대비 구현 범위

- 사진+GPS 단일 채널 신고, 동의 필수(SER-001), 신고 ID 기반 상태 조회(SER-003)
- AI 3-클래스 분류: 파인튜닝 모델(`model/finetuned.pt`)이 없으면 Gemini Vision API로 실시간
  분류, API 키가 없거나 호출이 실패하면 결정적 목업 분류기로 폴백한다.
- AI 재학습 센터: 시연용 시뮬레이션이 아니라 실제 파인튜닝. seed_images + 검수 확정 사진으로
  MobileNetV3-Small 분류 헤드를 학습시켜 model/finetuned.pt로 배포한다(위 "AI 분류·재학습
  동작 방식" 참고).
- 검수 큐 이관: LOW_CONF(top1<0.70 또는 margin<0.15), PATTERN_MISMATCH 구현.
  DISPUTE는 이의제기 채널이 MVP 범위 밖이라 미구현.
- 우선순위: 40/25/20/15 가중치, 컷오프 85/60/40. 조회 시점마다 경과시간 반영 재계산.
- 중복 신고: 동일 유형 + 반경 50m + 미완료 건이면 report_count 누적, 점수 갱신.
- 부서 배정: dept_mapping 목업 표(3개 유형 → 안전시설팀/시설관리파트).
- 검수: O(확정)/X(재분류 → corrected_by_dept 기록), audit_log에 수정 이력, O 비율 지표.
- 상태: 접수 → 배정 → 처리중 → 완료. 자동 확정 건은 '배정', 검수 대상은 '접수' 대기.
- 이미지: 저장 시 EXIF 제거 + 리사이즈. 번호판·얼굴 비식별(SER-002)은 로드맵.
