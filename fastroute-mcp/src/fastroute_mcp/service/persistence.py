from __future__ import annotations
import json, sqlite3, threading
from pathlib import Path
from typing import Any, Optional
from ..models.job import Job
from ..models.event import NormalizedEvent


class Persistence:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        c = self._conn
        c.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            state TEXT,
            board_path TEXT,
            board_sha256 TEXT,
            dsn_path TEXT,
            ses_path TEXT,
            report_path TEXT,
            routed_board_path TEXT,
            drc_report_path TEXT,
            config_hash TEXT,
            fastroute_version TEXT,
            fastroute_binary TEXT,
            kicad_version TEXT,
            parent_job_id TEXT,
            attempt_index INTEGER,
            profile TEXT,
            effective_config TEXT,
            argv TEXT,
            pid INTEGER,
            live_port INTEGER,
            exit_code INTEGER,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            stop_requested_at TEXT,
            workspace TEXT,
            initial_session_path TEXT,
            initial_session_hash TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            type TEXT,
            source TEXT,
            occurred_at TEXT,
            received_at TEXT,
            payload TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            artifact_type TEXT,
            path TEXT,
            sha256 TEXT,
            size INTEGER,
            created_at TEXT
        )""")
        c.commit()

    def save_job(self, job: Job):
        with self._lock:
            self._conn.execute("""INSERT OR REPLACE INTO jobs (
                job_id,state,board_path,board_sha256,dsn_path,ses_path,report_path,routed_board_path,
                drc_report_path,config_hash,fastroute_version,fastroute_binary,kicad_version,parent_job_id,
                attempt_index,profile,effective_config,argv,pid,live_port,exit_code,error_code,error_message,
                created_at,started_at,finished_at,stop_requested_at,workspace,initial_session_path,initial_session_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.job_id, job.state.value, job.board_path, job.board_sha256, job.dsn_path, job.ses_path,
                 job.report_path, job.routed_board_path, job.drc_report_path, job.config_hash,
                 job.fastroute_version, job.fastroute_binary, job.kicad_version, job.parent_job_id,
                 job.attempt_index, job.profile, json.dumps(job.effective_config), json.dumps(job.argv),
                 job.pid, job.live_port, job.exit_code, job.error_code, job.error_message,
                 job.created_at, job.started_at, job.finished_at, job.stop_requested_at, job.workspace,
                 job.initial_session_path, job.initial_session_hash))
            self._conn.commit()

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        # reconstruct
        job = Job(
            job_id=data["job_id"],
            created_at=data["created_at"],
            state=data["state"],
            board_path=data["board_path"],
            board_sha256=data["board_sha256"],
            dsn_path=data["dsn_path"],
            ses_path=data["ses_path"],
            report_path=data["report_path"],
            routed_board_path=data["routed_board_path"],
            drc_report_path=data["drc_report_path"],
            config_hash=data["config_hash"],
            fastroute_version=data["fastroute_version"],
            fastroute_binary=data["fastroute_binary"],
            kicad_version=data["kicad_version"],
            parent_job_id=data["parent_job_id"],
            attempt_index=data["attempt_index"] or 1,
            profile=data["profile"] or "balanced",
            effective_config=json.loads(data["effective_config"] or "{}"),
            argv=json.loads(data["argv"] or "[]"),
            pid=data["pid"],
            live_port=data["live_port"],
            exit_code=data["exit_code"],
            error_code=data["error_code"],
            error_message=data["error_message"],
            started_at=data["started_at"],
            finished_at=data["finished_at"],
            stop_requested_at=data["stop_requested_at"],
            workspace=data["workspace"],
            initial_session_path=data["initial_session_path"],
            initial_session_hash=data["initial_session_hash"],
        )
        return job

    def list_jobs(self, board_sha256: str | None = None, states: list[str] | None = None,
                  limit: int = 50, cursor: int = 0) -> list[Job]:
        q = "SELECT job_id FROM jobs"
        params = []
        conds = []
        if board_sha256:
            conds.append("board_sha256=?")
            params.append(board_sha256)
        if states:
            placeholders = ",".join("?" for _ in states)
            conds.append(f"state IN ({placeholders})")
            params.extend(states)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, cursor])
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            j = self.get_job(r["job_id"])
            if j:
                out.append(j)
        return out

    def count_jobs(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def add_event(self, ev: NormalizedEvent) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (job_id,type,source,occurred_at,received_at,payload) VALUES (?,?,?,?,?,?)",
                (ev.job_id, ev.type, ev.source, ev.occurred_at, ev.received_at, json.dumps(ev.payload)))
            self._conn.commit()
            return cur.lastrowid

    def get_events(self, job_id: str, after_event_id: int = 0, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id=? AND event_id>? ORDER BY event_id ASC LIMIT ?",
                (job_id, after_event_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    def max_event_id(self, job_id: str) -> int:
        with self._lock:
            r = self._conn.execute("SELECT MAX(event_id) FROM events WHERE job_id=?", (job_id,)).fetchone()
            return r[0] or 0

    def add_artifact(self, job_id: str, atype: str, path: str, sha256: str | None = None, size: int | None = None):
        import datetime
        with self._lock:
            self._conn.execute(
                "INSERT INTO artifacts (job_id,artifact_type,path,sha256,size,created_at) VALUES (?,?,?,?,?,?)",
                (job_id, atype, path, sha256, size, datetime.datetime.now(datetime.timezone.utc).isoformat()))
            self._conn.commit()
