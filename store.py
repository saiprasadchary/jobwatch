import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filters import _parse_timestamp

DB_PATH = Path(__file__).parent / "jobwatch.db"

VALID_STATUSES = ("new", "applied", "phone_screen", "interview", "offer", "rejected", "skipped")


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(seen_jobs)")}


def _ensure_seen_jobs_schema(conn: sqlite3.Connection) -> None:
    columns = _existing_columns(conn)
    added: set[str] = set()

    for name, definition in {
        "status": "TEXT DEFAULT 'new'",
        "status_updated": "TEXT",
        "posted_at": "TEXT",
        "source": "TEXT",
        "salary": "TEXT",
        "last_seen": "TEXT",
        "notified_at": "TEXT",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE seen_jobs ADD COLUMN {name} {definition}")
            added.add(name)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_jobs_notified_at ON seen_jobs(notified_at)")

    # Backfill legacy rows once when migrating older databases to the new schema.
    if "last_seen" in added:
        conn.execute("UPDATE seen_jobs SET last_seen = first_seen WHERE last_seen IS NULL")
    if "notified_at" in added:
        conn.execute("UPDATE seen_jobs SET notified_at = first_seen WHERE notified_at IS NULL")


def _ensure_source_runs_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            selected_lane TEXT NOT NULL,
            company TEXT NOT NULL,
            ats TEXT,
            source_lane TEXT,
            status TEXT NOT NULL,
            raw_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_runs_company ON source_runs(company, ats, run_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_runs_run_at ON source_runs(run_at DESC)")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_id     TEXT PRIMARY KEY,
            company    TEXT NOT NULL,
            title      TEXT NOT NULL,
            location   TEXT,
            url        TEXT,
            first_seen TEXT NOT NULL,
            status     TEXT DEFAULT 'new',
            status_updated TEXT
        )
        """
    )
    _ensure_seen_jobs_schema(conn)
    _ensure_source_runs_schema(conn)
    conn.commit()
    return conn


def _should_requeue_job(
    incoming_posted_at: str,
    existing_posted_at: str | None,
    notified_at: str | None,
    status: str | None,
) -> bool:
    if (status or "new") != "new":
        return False
    if not notified_at or not incoming_posted_at or incoming_posted_at == (existing_posted_at or ""):
        return False

    incoming_posted = _parse_timestamp(incoming_posted_at)
    if not incoming_posted:
        return False
    if incoming_posted < datetime.now(timezone.utc) - timedelta(days=3):
        return False

    existing_posted = _parse_timestamp(existing_posted_at)
    if existing_posted and incoming_posted <= existing_posted + timedelta(hours=1):
        return False
    return True


def sync_jobs(jobs: list[dict]) -> list[dict]:
    conn = _connect()
    cur = conn.cursor()
    pending = []
    seen_job_ids: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()

    for job in jobs:
        job_id = job["job_id"]
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        location = job.get("location", "")
        url = job.get("url", "")
        posted_at = job.get("posted_at", "")
        source = job.get("source", "")
        salary = job.get("salary", "")

        cur.execute(
            "SELECT first_seen, notified_at, posted_at, status FROM seen_jobs WHERE job_id = ?",
            (job_id,),
        )
        existing = cur.fetchone()
        if existing is None:
            pending.append({**job, "first_seen": now, "last_seen": now})
            cur.execute(
                """
                INSERT INTO seen_jobs (
                    job_id,
                    company,
                    title,
                    location,
                    url,
                    first_seen,
                    status,
                    posted_at,
                    source,
                    salary,
                    last_seen,
                    notified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, NULL)
                """,
                (
                    job_id,
                    job["company"],
                    job["title"],
                    location,
                    url,
                    now,
                    posted_at,
                    source,
                    salary,
                    now,
                ),
            )
            continue

        should_requeue = _should_requeue_job(posted_at, existing[2], existing[1], existing[3])

        cur.execute(
            """
            UPDATE seen_jobs
            SET company = ?,
                title = ?,
                location = COALESCE(NULLIF(?, ''), location),
                url = COALESCE(NULLIF(?, ''), url),
                posted_at = COALESCE(NULLIF(?, ''), posted_at),
                source = COALESCE(NULLIF(?, ''), source),
                salary = COALESCE(NULLIF(?, ''), salary),
                last_seen = ?,
                notified_at = CASE WHEN ? THEN NULL ELSE notified_at END
            WHERE job_id = ?
            """,
            (
                job["company"],
                job["title"],
                location,
                url,
                posted_at,
                source,
                salary,
                now,
                1 if should_requeue else 0,
                job_id,
            ),
        )

        if existing[1] is None or should_requeue:
            pending.append({**job, "first_seen": existing[0], "last_seen": now})

    conn.commit()
    conn.close()
    return pending


def filter_new_jobs(jobs: list[dict]) -> list[dict]:
    return sync_jobs(jobs)


def mark_jobs_notified(job_ids: Iterable[str]) -> int:
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return 0

    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE seen_jobs SET notified_at = ? WHERE job_id IN ({placeholders})",
        [now, *ids],
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()
    return updated


def _latest_successful_source_run(conn: sqlite3.Connection, company: str, ats: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT run_at, raw_count, matched_count, duration_seconds
        FROM source_runs
        WHERE company = ? AND ats = ? AND status = 'ok'
        ORDER BY run_at DESC
        LIMIT 1
        """,
        (company, ats),
    )
    return cur.fetchone()


def detect_source_anomalies(results: list[dict]) -> list[dict]:
    conn = _connect()
    anomalies = []

    for result in results:
        company = result.get("company", "Unknown")
        ats = result.get("ats", "")
        status = result.get("status", "")
        raw_count = int(result.get("raw_count") or 0)
        previous = _latest_successful_source_run(conn, company, ats)

        if status in {"error", "timeout"}:
            anomalies.append({
                **result,
                "anomaly": "source_failed",
                "detail": result.get("error") or status,
            })
            continue

        if status != "ok" or previous is None:
            continue

        previous_raw = int(previous["raw_count"] or 0)
        if previous_raw >= 20 and raw_count == 0:
            anomalies.append({
                **result,
                "anomaly": "zero_results",
                "detail": f"returned 0 jobs after previously returning {previous_raw}",
            })
        elif previous_raw >= 50 and raw_count < previous_raw * 0.2:
            anomalies.append({
                **result,
                "anomaly": "count_drop",
                "detail": f"raw job count dropped from {previous_raw} to {raw_count}",
            })

    conn.close()
    return anomalies


def record_source_results(results: list[dict], selected_lane: str) -> int:
    if not results:
        return 0

    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            now,
            selected_lane,
            result.get("company", "Unknown"),
            result.get("ats", ""),
            result.get("lane", ""),
            result.get("status", "unknown"),
            int(result.get("raw_count") or 0),
            int(result.get("matched_count") or 0),
            float(result.get("duration") or 0.0),
            result.get("error"),
        )
        for result in results
    ]
    conn.executemany(
        """
        INSERT INTO source_runs (
            run_at,
            selected_lane,
            company,
            ats,
            source_lane,
            status,
            raw_count,
            matched_count,
            duration_seconds,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def mark_status(job_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE seen_jobs SET status = ?, status_updated = ? WHERE job_id = ?",
        (status, now, job_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def search_jobs(query: str, limit: int = 20) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT
            job_id,
            company,
            title,
            location,
            url,
            status,
            first_seen,
            last_seen,
            posted_at,
            source,
            salary,
            notified_at
        FROM seen_jobs
        WHERE title LIKE ? OR company LIKE ?
        ORDER BY first_seen DESC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    )
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results


def get_status_summary() -> dict:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status ORDER BY COUNT(*) DESC")
    by_status = cur.fetchall()
    cur.execute(
        """
        SELECT company, title, url, status, first_seen, posted_at, source
        FROM seen_jobs
        WHERE status IN ('applied', 'phone_screen', 'interview', 'offer')
        ORDER BY status_updated DESC
        """
    )
    active = [
        {
            "company": r[0],
            "title": r[1],
            "url": r[2],
            "status": r[3],
            "first_seen": r[4],
            "posted_at": r[5],
            "source": r[6],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return {"by_status": by_status, "active_applications": active}


def get_recent_source_health(limit: int = 50) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            run_at,
            selected_lane,
            company,
            ats,
            source_lane,
            status,
            raw_count,
            matched_count,
            duration_seconds,
            error
        FROM source_runs
        ORDER BY run_at DESC, duration_seconds DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stats() -> dict:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM seen_jobs")
    total = cur.fetchone()[0]
    cur.execute("SELECT company, COUNT(*) FROM seen_jobs GROUP BY company ORDER BY COUNT(*) DESC")
    by_company = cur.fetchall()
    conn.close()
    return {"total": total, "by_company": by_company}
