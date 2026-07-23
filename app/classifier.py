"""AI 이미지 분류 (MVP_개발계획.md 4장)

우선순위: ① 재학습 센터에서 실제로 파인튜닝된 모델 파일(model/finetuned.pt)이 있으면
그걸 로드해 사용 ② 없고 GEMINI_API_KEY가 설정돼 있으면 Gemini Vision API로 실시간 분류
③ 둘 다 없으면(키 미설정·API 오류) 데모용 목업 분류기로 폴백. 목업은 이미지 바이트
해시 기반의 결정적 출력이라 같은 사진은 항상 같은 결과를 낸다(정확도 표본 점검·오프라인
데모 재현용).
"""
import hashlib
import io
import json
import os
from dataclasses import dataclass

CATEGORIES = ["도로 파손", "쓰레기 무단투기", "가로등 고장"]
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "model", "finetuned.pt")
GEMINI_MODEL = "gemini-flash-lite-latest"


@dataclass
class Prediction:
    category: str
    top1_prob: float
    top2_prob: float


def classify(image_bytes: bytes, category_user: str | None = None) -> Prediction:
    if os.path.exists(MODEL_PATH):
        return _classify_torch(image_bytes)
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return _classify_gemini(image_bytes, category_user)
        except Exception as e:  # 네트워크 오류·키 만료 등 — 신고 흐름은 끊기지 않게 목업으로 폴백
            print(f"[classifier] Gemini 호출 실패, 목업으로 폴백: {e}")
    return _classify_mock(image_bytes, category_user)


def _classify_gemini(image_bytes: bytes, category_user: str | None) -> Prediction:
    from google import genai
    from google.genai import types
    from PIL import Image

    fmt = (Image.open(io.BytesIO(image_bytes)).format or "JPEG").lower()
    mime = f"image/{'jpeg' if fmt == 'jpg' else fmt}"

    hint = f" 신고자는 '{category_user}' 유형으로 접수했으나, 참고만 하고 사진을 직접 보고 판단하라." \
        if category_user in CATEGORIES else ""
    prompt = (
        "이 사진은 시민이 제보한 생활 인프라 문제 사진이다. 사진을 보고 아래 3개 유형 중 "
        f"정확히 하나로만 분류하라: {', '.join(CATEGORIES)}." + hint +
        " top1은 가장 가능성 높은 유형과 확신도(0~1), top2_prob은 두 번째로 가능성 높은 "
        "유형의 확신도다. JSON으로만 답하라."
    )
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES},
                    "top1_prob": {"type": "number"},
                    "top2_prob": {"type": "number"},
                },
                "required": ["category", "top1_prob", "top2_prob"],
            },
        ),
    )
    data = json.loads(response.text)
    top1 = max(0.0, min(1.0, float(data["top1_prob"])))
    top2 = max(0.0, min(top1, float(data["top2_prob"])))
    return Prediction(data["category"], round(top1, 3), round(top2, 3))


def _classify_mock(image_bytes: bytes, category_user: str | None) -> Prediction:
    h = hashlib.sha256(image_bytes).digest()
    # 신고자가 3개 유형 중 하나를 선택했으면 그 유형을 top1로 (사전 선택 = AI 제안과 합치 가정)
    if category_user in CATEGORIES:
        category = category_user
    else:
        category = CATEGORIES[h[0] % len(CATEGORIES)]
    # top1 확률 0.55~0.98, top2와의 margin 0.05~0.40 — 일부 건이 LOW_CONF로 떨어져
    # 검수 큐 이관 흐름까지 데모 가능하도록 분포를 잡음
    top1 = 0.55 + (h[1] / 255) * 0.43
    margin = 0.05 + (h[2] / 255) * 0.35
    top2 = max(0.01, min(top1 - margin, 1 - top1))
    return Prediction(category, round(top1, 3), round(top2, 3))


def _classify_torch(image_bytes: bytes) -> Prediction:
    # 파인튜닝 모델 배포 시 활성화되는 경로 (경량 CNN, 224 리사이즈 — PER-001)
    import io
    import torch
    from PIL import Image
    from torchvision import transforms

    model = torch.jit.load(MODEL_PATH)
    model.eval()
    tf = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    with torch.no_grad():
        probs = torch.softmax(model(tf(img).unsqueeze(0)), dim=1)[0]
    top = torch.topk(probs, 2)
    return Prediction(CATEGORIES[top.indices[0].item()],
                      round(top.values[0].item(), 3),
                      round(top.values[1].item(), 3))


def review_reason(pred: Prediction, recent_corrections: int) -> str | None:
    """검수 큐 이관 조건 (계획서 4장). None이면 자동 배정 확정.

    DISPUTE(신고자 이의제기)는 이의제기 접수 채널이 MVP 범위 밖이라 미구현.
    """
    if pred.top1_prob < 0.70 or (pred.top1_prob - pred.top2_prob) < 0.15:
        return "LOW_CONF"
    if recent_corrections > 0:  # 동일 유형·행정동 반복 수정 이력
        return "PATTERN_MISMATCH"
    return None
