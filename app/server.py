"""접수 → 분류 → 우선순위 → 배정 → 검수 API 서버 (MVP_개발계획.md 2·6장)"""
import io
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from PIL import Image

from . import classifier, db, geo, priority, retrain

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")

DUP_RADIUS_M = 50          # 중복 신고 판정 반경 (계획서 3장)
STATUSES = ["접수", "배정", "처리중", "완료"]
CATEGORY_CHOICES = classifier.CATEGORIES + ["기타"]

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.json.ensure_ascii = False


def _row(mapping) -> dict:
    """sqlite Row → JSON dict. datetime은 ISO 문자열로(기본 jsonify는 HTTP-date라 화면 표시가 깨짐)."""
    return {k: (v.isoformat(sep=" ", timespec="seconds") if isinstance(v, datetime) else v)
            for k, v in dict(mapping).items()}


# ---------- 화면 2종 ----------
@app.get("/")
def report_page():
    return send_from_directory(STATIC_DIR, "report.html")


@app.get("/admin")
def admin_page():
    return send_from_directory(STATIC_DIR, "admin.html")


@app.get("/uploads/<path:name>")
def uploaded_image(name):
    return send_from_directory(UPLOAD_DIR, name)


# ---------- 시민 신고 ----------
@app.post("/api/complaints")
def create_complaint():
    if request.form.get("consent") != "true":  # SER-001
        return jsonify({"error": "정보 사용 동의가 필요합니다."}), 400
    file = request.files.get("photo")
    if not file:
        return jsonify({"error": "사진이 필요합니다."}), 400
    try:
        lat, lng = float(request.form["lat"]), float(request.form["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "GPS 위치(lat, lng)가 필요합니다."}), 400

    category_user = (request.form.get("category_user") or "").strip() or None
    image_bytes = file.read()
    reported_at = datetime.now()

    pred = classifier.classify(image_bytes, category_user)
    admin_dong = geo.to_admin_dong(lat, lng)
    loc_score, loc_note = geo.location_sensitivity(lat, lng)

    conn = db.connect()
    try:
        # 중복 신고 누적: 동일 유형 + 반경 50m + 미완료 건 → report_count += 1
        for row in conn.execute(
                "SELECT id, lat, lng, report_count, reported_at, category, location_note "
                "FROM complaints WHERE category=? AND status != '완료'", (pred.category,)):
            if geo.haversine_m(lat, lng, row["lat"], row["lng"]) <= DUP_RADIUS_M:
                count = row["report_count"] + 1
                score, grade = priority.priority(
                    row["category"], geo.location_sensitivity(row["lat"], row["lng"])[0],
                    count, _ts(row["reported_at"]))
                conn.execute(
                    "UPDATE complaints SET report_count=?, priority_score=?, priority_grade=? WHERE id=?",
                    (count, score, grade, row["id"]))
                conn.commit()
                return jsonify({"id": row["id"], "duplicate_merged": True,
                                "report_count": count, "priority_score": score,
                                "priority_grade": grade, "category": row["category"]}), 200

        # 신규 접수
        try:
            image_url = _save_image(image_bytes)
        except Exception:
            # HEIC 등 Pillow가 못 여는 포맷 — 미처리 시 500(HTML)이 떨어져 프론트의
            # res.json() 파싱이 조용히 깨지고 신고가 사라진 것처럼 보인다 (버그 리포트로 발견).
            return jsonify({"error": "지원하지 않는 사진 형식입니다. JPG 또는 PNG로 다시 시도해주세요."}), 400
        recent_corr = conn.execute(
            "SELECT COUNT(*) FROM complaints WHERE corrected_by_dept IS NOT NULL "
            "AND category=? AND admin_dong=?", (pred.category, admin_dong)).fetchone()[0]
        reason = classifier.review_reason(pred, recent_corr)
        department = db.assign_department(conn, admin_dong, pred.category)
        status = "배정" if (reason is None and department) else "접수"  # 검수 대상은 접수 상태로 대기
        score, grade = priority.priority(pred.category, loc_score, 1, reported_at)

        cur = conn.execute(
            "INSERT INTO complaints (reported_at, lat, lng, admin_dong, category, category_user,"
            " report_count, department, status, priority_score, priority_grade, confidence,"
            " review_reason, location_note, image_url, consent)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (reported_at, lat, lng, admin_dong, pred.category, category_user, 1, department,
             status, score, grade, pred.top1_prob, reason, loc_note, image_url, True))
        conn.commit()
        return jsonify({"id": cur.lastrowid, "duplicate_merged": False,
                        "category": pred.category, "confidence": pred.top1_prob,
                        "review_reason": reason, "department": department,
                        "priority_score": score, "priority_grade": grade,
                        "admin_dong": admin_dong, "status": status}), 201
    finally:
        conn.close()


@app.get("/api/complaints/<int:cid>/status")
def complaint_status(cid):
    """신고 ID 기반 상태 조회 — 식별 정보 미수집 (SER-003)"""
    conn = db.connect()
    row = conn.execute(
        "SELECT id, category, status, priority_grade, reported_at, report_count "
        "FROM complaints WHERE id=?", (cid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "해당 신고를 찾을 수 없습니다."}), 404
    return jsonify(_row(row))


# ---------- 담당자 관리 ----------
@app.get("/api/complaints")
def list_complaints():
    conn = db.connect()
    rows = [_row(r) for r in conn.execute("SELECT * FROM complaints")]
    # 경과 시간·빈도 변동을 반영해 조회 시점 점수로 갱신
    for r in rows:
        score, grade = priority.priority(
            r["category"], geo.location_sensitivity(r["lat"], r["lng"])[0],
            r["report_count"], _ts(r["reported_at"]))
        if score != r["priority_score"]:
            conn.execute("UPDATE complaints SET priority_score=?, priority_grade=? WHERE id=?",
                         (score, grade, r["id"]))
            r["priority_score"], r["priority_grade"] = score, grade
    conn.commit()
    conn.close()
    rows.sort(key=lambda r: r["priority_score"], reverse=True)
    return jsonify(rows)


@app.post("/api/complaints/<int:cid>/review")
def review_complaint(cid):
    """O/X 검수: O는 분류 확정, X는 new_category로 수동 재분류 (COR-001)"""
    body = request.get_json(force=True)
    ox = body.get("ox")
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()
        if not row:
            return jsonify({"error": "해당 신고를 찾을 수 없습니다."}), 404
        now = datetime.now()

        if ox == "O":
            department = row["department"] or db.assign_department(conn, row["admin_dong"], row["category"])
            conn.execute("UPDATE complaints SET review_reason=NULL, department=?, "
                         "status=CASE WHEN status='접수' THEN '배정' ELSE status END WHERE id=?",
                         (department, cid))
            conn.execute("INSERT INTO audit_log (complaint_id, at, action, before_value, after_value)"
                         " VALUES (?,?,?,?,?)", (cid, now, "REVIEW_O", row["category"], row["category"]))
        elif ox == "X":
            new_cat = body.get("new_category")
            if new_cat not in CATEGORY_CHOICES:
                return jsonify({"error": f"new_category는 {CATEGORY_CHOICES} 중 하나여야 합니다."}), 400
            department = db.assign_department(conn, row["admin_dong"], new_cat)
            score, grade = priority.priority(
                new_cat, geo.location_sensitivity(row["lat"], row["lng"])[0],
                row["report_count"], _ts(row["reported_at"]))
            conn.execute(
                "UPDATE complaints SET category=?, corrected_by_dept=?, review_reason=NULL,"
                " department=?, priority_score=?, priority_grade=?,"
                " status=CASE WHEN status='접수' THEN '배정' ELSE status END WHERE id=?",
                (new_cat, new_cat, department, score, grade, cid))
            conn.execute("INSERT INTO audit_log (complaint_id, at, action, before_value, after_value)"
                         " VALUES (?,?,?,?,?)", (cid, now, "RECLASSIFY", row["category"], new_cat))
        else:
            return jsonify({"error": "ox는 'O' 또는 'X'여야 합니다."}), 400
        conn.commit()
        updated = _row(conn.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone())
        return jsonify(updated)
    finally:
        conn.close()


@app.post("/api/complaints/<int:cid>/status")
def change_status(cid):
    """처리 상태 변경: 접수 → 배정 → 처리중 → 완료 (SFR-006)"""
    new_status = request.get_json(force=True).get("status")
    if new_status not in STATUSES:
        return jsonify({"error": f"status는 {STATUSES} 중 하나여야 합니다."}), 400
    conn = db.connect()
    try:
        row = conn.execute("SELECT status FROM complaints WHERE id=?", (cid,)).fetchone()
        if not row:
            return jsonify({"error": "해당 신고를 찾을 수 없습니다."}), 404
        conn.execute("UPDATE complaints SET status=? WHERE id=?", (new_status, cid))
        conn.execute("INSERT INTO audit_log (complaint_id, at, action, before_value, after_value)"
                     " VALUES (?,?,?,?,?)", (cid, datetime.now(), "STATUS_CHANGE", row["status"], new_status))
        conn.commit()
        return jsonify({"id": cid, "status": new_status})
    finally:
        conn.close()


@app.get("/api/complaints/<int:cid>/history")
def complaint_history(cid):
    conn = db.connect()
    rows = [_row(r) for r in conn.execute(
        "SELECT at, action, before_value, after_value FROM audit_log WHERE complaint_id=? ORDER BY at", (cid,))]
    conn.close()
    return jsonify(rows)


@app.get("/api/metrics")
def metrics():
    """O 비율 = 분류 신뢰도 지표 (계획서 6장 3항)"""
    conn = db.connect()
    o = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='REVIEW_O'").fetchone()[0]
    x = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='RECLASSIFY'").fetchone()[0]
    conn.close()
    total = o + x
    return jsonify({"reviewed": total, "correct": o,
                    "accuracy": round(o / total, 3) if total else None})


# ---------- 건의사항 (민원 분류 대상이 아닌 자유 의견) ----------
@app.post("/api/feedback")
def create_feedback():
    body = request.get_json(force=True) or {}
    content = (body.get("content") or "").strip()
    contact = (body.get("contact") or "").strip() or None
    if not content:
        return jsonify({"error": "내용을 입력해주세요."}), 400
    conn = db.connect()
    cur = conn.execute("INSERT INTO feedback (submitted_at, content, contact) VALUES (?,?,?)",
                       (datetime.now(), content, contact))
    conn.commit()
    conn.close()
    return jsonify({"id": cur.lastrowid}), 201


@app.get("/api/feedback")
def list_feedback():
    conn = db.connect()
    rows = [_row(r) for r in conn.execute("SELECT * FROM feedback ORDER BY submitted_at DESC")]
    conn.close()
    return jsonify(rows)


# ---------- AI 재학습 (app/retrain.py — seed_images + 검수 데이터로 실제 파인튜닝) ----------
@app.post("/api/retrain/start")
def retrain_start():
    return jsonify(retrain.start())


@app.get("/api/retrain/status")
def retrain_status():
    return jsonify(retrain.status())


@app.get("/api/retrain/history")
def retrain_history():
    return jsonify(retrain.history())


def _save_image(image_bytes: bytes) -> str:
    """EXIF(GPS 등 메타데이터) 제거 후 리사이즈 저장 — 최소 수집 원칙(SER-002/003 보조).
    번호판·얼굴 비식별 처리는 탐지 모델 필요로 로드맵 항목."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((1280, 1280))
    name = f"{uuid.uuid4().hex}.jpg"
    img.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=85)
    return f"/uploads/{name}"


def _ts(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def create_app():
    db.init_db()
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8000, debug=True)
