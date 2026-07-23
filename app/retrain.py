"""AI 재학습 — 실제 파인튜닝 (로드맵 항목 실구현)

seed_images/의 시드 사진과 담당자 검수(O/X)로 확정된 신고 사진을 모아
ImageNet 사전학습 MobileNetV3-Small 위에 새 3-클래스 분류 헤드를 실제로 학습시킨다.
정확도·손실은 매 epoch 검증셋에서 직접 측정한 실측치이며, 학습이 끝나면
model/finetuned.pt로 저장되어 classifier.py가 다음 분류부터 자동으로 이 모델을 쓴다.
/api/retrain/status 응답 형식은 기존 시뮬레이션 버전과 동일하게 유지해 admin.html은
수정 없이 그대로 동작한다.
"""
import json
import os
import random
import threading
import time
from datetime import datetime

from . import classifier, db

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
MODEL_DIR = os.path.join(BASE_DIR, "model")
SEED_DIR = os.path.join(BASE_DIR, "seed_images")
HISTORY_PATH = os.path.join(MODEL_DIR, "history.json")
CATEGORIES = classifier.CATEGORIES

TOTAL_EPOCHS = 12
LR = 3e-3
BATCH_SIZE = 8
VAL_RATIO = 0.2
MIN_EPOCH_SECONDS = 0.8  # 데이터가 적어 즉시 끝나버리는 걸 막는 최소 표시 시간(계산 자체는 실측)
IMG_EXTS = (".png", ".jpg", ".jpeg")

# seed_images/ 폴더명은 축약형이라 classifier.CATEGORIES 표기와 다르다 (예: "가로등" → "가로등 고장")
SEED_DIR_TO_CATEGORY = {"가로등": "가로등 고장", "도로파손": "도로 파손", "쓰레기": "쓰레기 무단투기"}

_job = None
_lock = threading.Lock()


def _collect_dataset() -> list[tuple[str, int]]:
    """시드 사진 + 검수로 확정된 신고 사진 경로를 (경로, 라벨) 목록으로 수집."""
    items = []
    for dirname, cat in SEED_DIR_TO_CATEGORY.items():
        d = os.path.join(SEED_DIR, dirname)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.lower().endswith(IMG_EXTS):
                items.append((os.path.join(d, fn), CATEGORIES.index(cat)))

    conn = db.connect()
    placeholders = ",".join("?" * len(CATEGORIES))
    rows = conn.execute(
        f"SELECT DISTINCT c.image_url, c.category FROM complaints c "
        f"JOIN audit_log a ON a.complaint_id = c.id "
        f"WHERE a.action IN ('REVIEW_O','RECLASSIFY') AND c.category IN ({placeholders})",
        CATEGORIES).fetchall()
    conn.close()
    for r in rows:
        if not r["image_url"]:
            continue
        path = os.path.join(BASE_DIR, r["image_url"].lstrip("/"))
        if os.path.exists(path):
            items.append((path, CATEGORIES.index(r["category"])))
    return items


def _build_model():
    import torch.nn as nn
    from torchvision import models

    m = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    for p in m.parameters():
        p.requires_grad = False
    in_f = m.classifier[-1].in_features
    m.classifier[-1] = nn.Linear(in_f, len(CATEGORIES))
    return m


def _transform():
    from torchvision import transforms
    return transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])


def _load_batch(items, tf):
    import torch
    from PIL import Image

    X = torch.stack([tf(Image.open(p).convert("RGB")) for p, _ in items])
    y = torch.tensor([label for _, label in items])
    return X, y


def _stratified_split(items: list[tuple[str, int]]):
    """클래스별 비율을 유지한 train/val 분리 — val셋이 특정 클래스로 쏠리는 것을 방지."""
    by_class: dict[int, list[str]] = {}
    for path, label in items:
        by_class.setdefault(label, []).append(path)
    train_items, val_items = [], []
    for label, paths in by_class.items():
        random.shuffle(paths)
        n_val = max(1, round(len(paths) * VAL_RATIO))
        val_items += [(p, label) for p in paths[:n_val]]
        train_items += [(p, label) for p in paths[n_val:]]
    return train_items, val_items


def _class_counts(items: list[tuple[str, int]]) -> dict[str, int]:
    counts = {cat: 0 for cat in CATEGORIES}
    for _, label in items:
        counts[CATEGORIES[label]] += 1
    return counts


def _load_history() -> list[dict]:
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _append_history(record: dict) -> None:
    records = _load_history()
    records.append(record)
    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def history() -> list[dict]:
    return _load_history()


def _evaluate(model, X, y, loss_fn) -> tuple[float, float]:
    import torch

    if X.size(0) == 0:
        return 0.0, 0.0
    model.eval()
    with torch.no_grad():
        out = model(X)
        loss = loss_fn(out, y).item()
        acc = (out.argmax(1) == y).float().mean().item()
    return acc, loss


def start() -> dict:
    global _job
    with _lock:
        if _job is not None and _job.get("state") == "running":
            return status()
        prev_version = _job["version"] if _job else 1
        _job = {
            "state": "running", "started_at": time.time(), "epoch": 0,
            "total_epochs": TOTAL_EPOCHS, "loss": None, "accuracy": None,
            "baseline_accuracy": None, "target_accuracy": None,
            "sample_count": 0, "version": prev_version + 1, "progress": 0.0,
            "class_counts": {}, "history": [],
            "logs": ["학습 데이터 수집 중..."],
        }
        job = _job
    threading.Thread(target=_train_job, args=(job,), daemon=True).start()
    return status()


def status() -> dict:
    if _job is None:
        return {"state": "idle"}
    return {k: v for k, v in _job.items() if k != "started_at"}


def _train_job(job: dict) -> None:
    import torch
    import torch.nn as nn

    try:
        items = _collect_dataset()
        if len(items) < len(CATEGORIES) * 4:
            job["state"] = "error"
            job["logs"].append(f"오류: 학습 가능한 사진이 너무 적습니다 ({len(items)}장). "
                                "seed_images에 유형별 사진을 더 추가하세요.")
            return

        train_items, val_items = _stratified_split(items)
        job["sample_count"] = len(items)
        job["class_counts"] = _class_counts(items)
        job["logs"].append(f"학습 데이터 로드 — 총 {len(items)}장 (train {len(train_items)} / val {len(val_items)})")

        tf = _transform()
        train_X, train_y = _load_batch(train_items, tf)
        val_X, val_y = _load_batch(val_items, tf)

        model = _build_model()
        loss_fn = nn.CrossEntropyLoss()
        base_acc, _ = _evaluate(model, val_X, val_y, loss_fn)
        job["baseline_accuracy"] = round(base_acc, 4)

        opt = torch.optim.Adam(model.classifier[-1].parameters(), lr=LR, weight_decay=1e-4)

        best_acc, best_epoch, best_state = -1.0, 0, None
        for epoch in range(1, TOTAL_EPOCHS + 1):
            t0 = time.time()
            model.train()
            perm = torch.randperm(train_X.size(0))
            for i in range(0, train_X.size(0), BATCH_SIZE):
                idx = perm[i:i + BATCH_SIZE]
                opt.zero_grad()
                loss = loss_fn(model(train_X[idx]), train_y[idx])
                loss.backward()
                opt.step()

            val_acc, val_loss = _evaluate(model, val_X, val_y, loss_fn)
            if val_acc > best_acc:
                best_acc, best_epoch = val_acc, epoch
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            elapsed = time.time() - t0
            if elapsed < MIN_EPOCH_SECONDS:
                time.sleep(MIN_EPOCH_SECONDS - elapsed)

            job["epoch"] = epoch
            job["loss"] = round(val_loss, 3)
            job["accuracy"] = round(val_acc, 4)
            job["progress"] = round(epoch / TOTAL_EPOCHS * 100, 1)
            job["history"].append({"epoch": epoch, "loss": job["loss"], "accuracy": job["accuracy"]})
            job["logs"].append(f"Epoch {epoch}/{TOTAL_EPOCHS} - loss: {val_loss:.3f} - val_acc: {val_acc:.3f}")

        # 마지막 epoch가 아니라 검증 정확도가 가장 높았던 epoch의 가중치를 배포 — 과적합/막판 진동 방지
        model.load_state_dict(best_state)
        job["accuracy"] = round(best_acc, 4)
        job["target_accuracy"] = round(best_acc, 4)
        job["logs"].append(f"최적 체크포인트 선택 — Epoch {best_epoch} (val_acc: {best_acc:.3f})")
        os.makedirs(MODEL_DIR, exist_ok=True)
        model.eval()
        with torch.no_grad():
            scripted = torch.jit.trace(model, train_X[:1])
        scripted.save(os.path.join(MODEL_DIR, f"finetuned_v{job['version']}.pt"))
        scripted.save(os.path.join(MODEL_DIR, "finetuned.pt"))

        delta = (job["target_accuracy"] - job["baseline_accuracy"]) * 100
        job["logs"].append(f"모델 저장 — model/finetuned_v{job['version']}.pt")
        job["logs"].append(
            f"배포 완료 — 정확도 {job['baseline_accuracy']*100:.1f}% → "
            f"{job['target_accuracy']*100:.1f}% ({'+' if delta >= 0 else ''}{delta:.1f}%p)")
        job["state"] = "done"
        job["progress"] = 100.0
        _append_history({
            "version": job["version"], "completed_at": datetime.now().isoformat(timespec="seconds"),
            "sample_count": job["sample_count"], "class_counts": job["class_counts"],
            "baseline_accuracy": job["baseline_accuracy"], "target_accuracy": job["target_accuracy"],
        })
    except Exception as e:
        job["state"] = "error"
        job["logs"].append(f"오류: {e}")
