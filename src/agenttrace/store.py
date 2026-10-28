"""SQLite persistence for run metadata and append-only execution events."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from agenttrace.models import RunRecord, TraceEvent, utc_now
from agenttrace.security import redact


class SQLiteTraceStore:
    """Thread-safe SQLite store suitable for local demos and small evaluation runs."""

    def __init__(self, path: str | Path = "agenttrace.sqlite3") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    final_answer TEXT,
                    steps INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(run_id, seq),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )"""
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq)")
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(runs)")}
            for name, default in (("mode", "mock"), ("model", "deterministic-mock")):
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS evaluation_batches (
                batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                manifest_json TEXT NOT NULL, config_json TEXT NOT NULL)""")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS evaluation_results (
                batch_id TEXT NOT NULL, scenario_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(batch_id, scenario_id),
                FOREIGN KEY(batch_id) REFERENCES evaluation_batches(batch_id))""")
            for table in ("evaluation_batches", "evaluation_results"):
                for action in ("UPDATE", "DELETE"):
                    self._conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'Evaluation records are immutable'); END""")

    def create_run(self, run_id: str, task: str, *, mode: str = "mock",
                   model: str = "deterministic-mock") -> RunRecord:
        started_at = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs(run_id, task, status, steps, started_at, mode, model) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, redact(task), "running", 0, started_at.isoformat(), mode, redact(model)),
            )
        return RunRecord(run_id=run_id, task=redact(task), status="running", started_at=started_at,
                         mode=mode, model=redact(model))

    def append_event(self, event: TraceEvent) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO events(run_id, seq, event_type, timestamp, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.run_id,
                    event.seq,
                    event.event_type,
                    event.timestamp.isoformat(),
                    json.dumps(redact(event.payload), ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        final_answer: str | None,
        steps: int,
    ) -> RunRecord:
        finished_at = utc_now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE runs SET status = ?, final_answer = ?, steps = ?, finished_at = ?
                   WHERE run_id = ? AND status = 'running'""",
                (status, redact(final_answer), steps, finished_at.isoformat(), run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Run not found: {run_id}")
        record = self.get_run(run_id)
        if record is None:
            raise KeyError(f"Run not found after update: {run_id}")
        return record

    def list_runs(self, *, limit: int = 20) -> list[RunRecord]:
        """Return the most recently started runs for the local workbench."""
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            RunRecord(
                run_id=row["run_id"],
                mode=row["mode"], model=row["model"],
                task=row["task"],
                status=row["status"],
                final_answer=row["final_answer"],
                steps=row["steps"],
                started_at=datetime.fromisoformat(row["started_at"]),
                finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            )
            for row in rows
        ]

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            mode=row["mode"], model=row["model"],
            task=row["task"],
            status=row["status"],
            final_answer=row["final_answer"],
            steps=row["steps"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        )

    def get_events(self, run_id: str) -> list[TraceEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
            ).fetchall()
        return [
            TraceEvent(
                run_id=row["run_id"],
                seq=row["seq"],
                event_type=row["event_type"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_batch(self, batch_id: str, manifest: dict, config: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO evaluation_batches VALUES (?, ?, ?, ?)",
                (batch_id, utc_now().isoformat(), json.dumps(redact(manifest), ensure_ascii=False),
                 json.dumps(redact(config), ensure_ascii=False)))

    def append_evaluation(self, batch_id: str, scenario_id: str, result: dict) -> None:
        with self._lock, self._conn:
            if not self._conn.execute("SELECT 1 FROM evaluation_batches WHERE batch_id=?", (batch_id,)).fetchone():
                raise KeyError("Unknown batch")
            self._conn.execute("INSERT INTO evaluation_results VALUES (?, ?, ?)",
                               (batch_id, scenario_id, json.dumps(redact(result), ensure_ascii=False)))

    def get_batch(self, batch_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM evaluation_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                return None
            results = self._conn.execute("SELECT payload_json FROM evaluation_results WHERE batch_id=? ORDER BY scenario_id", (batch_id,)).fetchall()
        return {"batch_id": batch_id, "created_at": row["created_at"],
                "manifest": json.loads(row["manifest_json"]), "config": json.loads(row["config_json"]),
                "results": [json.loads(r[0]) for r in results]}

    def list_batches(self, limit: int = 30) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be in 1..100")
        with self._lock:
            rows = self._conn.execute("SELECT batch_id FROM evaluation_batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.get_batch(r[0]) for r in rows]
