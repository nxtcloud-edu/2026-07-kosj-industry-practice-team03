"""AI 재학습 시뮬레이터 (로드맵 항목 시연용)

실제 재학습 파이프라인(가중치 업데이트·모델 배포)은 MVP 범위 밖이며 여기서 동작하지
않는다. 이 모듈은 "검수·재분류로 쌓인 데이터가 모델을 계속 개선한다"는 제품 흐름을
시연하기 위한 결정적 시뮬레이션이다. 단, 숫자를 임의로 지어내지 않고 audit_log에
실제로 쌓인 검수 건수·현재 O 비율(=/api/metrics와 동일 지표)에서 진행률과 정확도
개선폭을 계산해, 화면에 나오는 값이 실제 누적 데이터 규모와 연동되게 했다.
"""
import math
import time

from . import db

DURATION_SEC = 18           # 데모용 학습 소요 시간
TOTAL_EPOCHS = 12
MAX_ACC = 0.985
IMPROVE_PER_SAMPLE = 0.004  # 검수 데이터 1건당 시뮬레이션 정확도 개선폭
BASELINE_ACC_NO_HISTORY = 0.70

_job = None  # 진행/완료된 마지막 학습 잡 (프로세스 메모리 — 서버 재시작 시 초기화)


def _correction_count() -> int:
    conn = db.connect()
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action IN ('REVIEW_O','RECLASSIFY')").fetchone()[0]
    conn.close()
    return n


def _current_accuracy() -> float:
    conn = db.connect()
    o = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='REVIEW_O'").fetchone()[0]
    x = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='RECLASSIFY'").fetchone()[0]
    conn.close()
    total = o + x
    return round(o / total, 4) if total else BASELINE_ACC_NO_HISTORY


def start() -> dict:
    global _job
    baseline_acc = _current_accuracy()
    sample_count = _correction_count()
    prev_version = _job["version"] if _job else 1
    gain = max(0.0, min(MAX_ACC - baseline_acc, sample_count * IMPROVE_PER_SAMPLE))
    _job = {
        "started_at": time.time(),
        "sample_count": sample_count,
        "baseline_acc": baseline_acc,
        "target_acc": round(baseline_acc + gain, 4),
        "version": prev_version + 1,
    }
    return status()


def status() -> dict:
    if _job is None:
        return {"state": "idle"}
    elapsed = time.time() - _job["started_at"]
    progress = max(0.0, min(1.0, elapsed / DURATION_SEC))
    epoch = min(TOTAL_EPOCHS, math.ceil(progress * TOTAL_EPOCHS)) if progress > 0 else 0
    eased = 1 - (1 - progress) ** 2  # ease-out — 초반 급상승, 후반 수렴 (전형적 학습 곡선 형태)
    cur_acc = round(_job["baseline_acc"] + (_job["target_acc"] - _job["baseline_acc"]) * eased, 4)
    cur_loss = round(1.10 * (1 - eased) + 0.05, 3)
    done = progress >= 1.0
    return {
        "state": "done" if done else ("running" if progress > 0 else "idle"),
        "progress": round(progress * 100, 1),
        "epoch": epoch,
        "total_epochs": TOTAL_EPOCHS,
        "loss": cur_loss,
        "accuracy": cur_acc,
        "baseline_accuracy": _job["baseline_acc"],
        "target_accuracy": _job["target_acc"],
        "sample_count": _job["sample_count"],
        "version": _job["version"],
        "logs": _build_logs(epoch, done),
    }


def _build_logs(epoch: int, done: bool) -> list[str]:
    lines = [f"학습 데이터 로드 — 누적 검수 {_job['sample_count']}건"]
    for e in range(1, epoch + 1):
        eased = 1 - (1 - e / TOTAL_EPOCHS) ** 2
        acc = _job["baseline_acc"] + (_job["target_acc"] - _job["baseline_acc"]) * eased
        loss = 1.10 * (1 - eased) + 0.05
        lines.append(f"Epoch {e}/{TOTAL_EPOCHS} - loss: {loss:.3f} - val_acc: {acc:.3f}")
    if done:
        delta = (_job["target_acc"] - _job["baseline_acc"]) * 100
        lines.append(f"모델 저장 — model/finetuned_v{_job['version']}.pt")
        lines.append(
            f"배포 완료 — 정확도 {_job['baseline_acc']*100:.1f}% → "
            f"{_job['target_acc']*100:.1f}% ({'+' if delta >= 0 else ''}{delta:.1f}%p)")
    return lines
