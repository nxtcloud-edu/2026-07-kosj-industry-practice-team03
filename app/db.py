"""통합 DB (MVP_개발계획.md 3장) — SQLite 데모 구성"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "complaints.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS complaints (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  reported_at     TIMESTAMP NOT NULL,
  lat             DECIMAL(9,6) NOT NULL,
  lng             DECIMAL(9,6) NOT NULL,
  admin_dong      VARCHAR(30),
  category        VARCHAR(30),
  category_user   VARCHAR(30),
  report_count    INT DEFAULT 1,
  department      VARCHAR(30),
  status          VARCHAR(15) DEFAULT '접수',
  priority_score  INT,
  priority_grade  VARCHAR(10),
  confidence      DECIMAL(4,3),
  review_reason   VARCHAR(20),
  corrected_by_dept VARCHAR(30),
  location_note   VARCHAR(50),
  image_url       TEXT,
  consent         BOOLEAN NOT NULL
);

-- 행정동 x 민원 종류 x 담당 부서 목업 매핑 표 (DAR-003)
CREATE TABLE IF NOT EXISTS dept_mapping (
  admin_dong  VARCHAR(30),
  category    VARCHAR(30),
  team        VARCHAR(30),
  part        VARCHAR(30)
);

-- 수정 이력 (SFR-005, COR-001)
CREATE TABLE IF NOT EXISTS audit_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  complaint_id INT NOT NULL,
  at           TIMESTAMP NOT NULL,
  action       VARCHAR(30),   -- REVIEW_O / RECLASSIFY / STATUS_CHANGE
  before_value VARCHAR(50),
  after_value  VARCHAR(50)
);
"""

# 부서 매핑 규칙 (MVP 3개 유형 — 모든 행정동 공통, '*')
DEPT_RULES = [
    ("*", "도로 파손", "안전시설팀", "시설관리파트"),      # 시설유지 및 점검
    ("*", "가로등 고장", "안전시설팀", "시설관리파트"),    # 시설유지 및 점검
    ("*", "쓰레기 무단투기", "안전시설팀", "시설관리파트"), # 보도물 청소
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM dept_mapping").fetchone()[0] == 0:
        conn.executemany("INSERT INTO dept_mapping VALUES (?,?,?,?)", DEPT_RULES)
    conn.commit()
    conn.close()


def assign_department(conn: sqlite3.Connection, admin_dong: str, category: str) -> str | None:
    """유형 x 행정동 기준 담당 부서 자동 배정 (SFR-004). 매핑 없으면 None(미배정)."""
    row = conn.execute(
        "SELECT team, part FROM dept_mapping WHERE category=? AND admin_dong IN (?, '*') "
        "ORDER BY CASE admin_dong WHEN ? THEN 0 ELSE 1 END LIMIT 1",
        (category, admin_dong, admin_dong)).fetchone()
    return f"{row['team']}/{row['part']}" if row else None
