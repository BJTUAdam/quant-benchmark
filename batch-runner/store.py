"""SQLite 任务状态表。状态：pending / running / completed / failed。"""
from __future__ import annotations

import json
import hashlib
import os
import pathlib
import sqlite3

import spec as specmod


class StoreError(RuntimeError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
    job_id TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    task TEXT NOT NULL,
    overrides TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    attempt_limit INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    pid INTEGER,
    not_before REAL NOT NULL DEFAULT 0,
    started_at REAL,
    finished_at REAL,
    exit_code INTEGER,
    error TEXT,
    history TEXT NOT NULL DEFAULT '[]'
)"""
ACTIVE = ("pending", "running")


class Store:
    def __init__(self, path):
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute(SCHEMA)

    def close(self):
        self.db.close()

    # ---- 查询 ----
    def get(self, job_id):
        return self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def all(self):
        return self.db.execute("SELECT * FROM jobs ORDER BY ordinal, job_id").fetchall()

    def with_status(self, status):
        return self.db.execute("SELECT * FROM jobs WHERE status=? ORDER BY ordinal, job_id", (status,)).fetchall()

    def counts(self):
        out = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
        for row in self.db.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status"):
            out[row["status"]] = row["n"]
        return out

    def next_pending(self, now):
        return self.db.execute(
            "SELECT * FROM jobs WHERE status='pending' AND not_before<=? ORDER BY ordinal, job_id LIMIT 1",
            (now,)).fetchone()

    def has_active(self):
        return self.db.execute("SELECT 1 FROM jobs WHERE status IN ('pending','running') LIMIT 1").fetchone() is not None

    # ---- 写入 ----
    def _update(self, job_id, **fields):
        names = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {names} WHERE job_id=?", (*fields.values(), job_id))

    def sync(self, spec):
        """登记批次里的任务。已登记任务的定义不能被悄悄改掉。"""
        self.db.execute("SAVEPOINT sync_jobs")
        try:
            added = self._sync(spec)
        except BaseException:
            self.db.execute("ROLLBACK TO sync_jobs")
            raise
        finally:
            self.db.execute("RELEASE sync_jobs")
        return added

    def _sync(self, spec):
        self.db.execute("CREATE TABLE IF NOT EXISTS batch_settings (id INTEGER PRIMARY KEY, digest TEXT)")
        fields = ("template", "runner_script", "runner_root", "python", "command_prefix", "env")
        signature = hashlib.sha256(specmod.canonical({k: spec[k] for k in fields}).encode()).hexdigest()
        saved = self.db.execute("SELECT digest FROM batch_settings WHERE id=1").fetchone()
        if saved and saved["digest"] != signature:
            raise StoreError("批次执行配置已冻结。模板、执行入口或环境发生变化，请使用新 batch_id")
        existing = self.all()
        jobs = {job["job_id"]: job for job in spec["jobs"]}
        if any(row["job_id"] not in jobs for row in existing):
            raise StoreError("清单缺少已登记的任务，请恢复任务或使用新 batch_id")
        if not saved:
            for row in existing:
                cfg = specmod.build_config(spec, jobs[row["job_id"]], row["attempt"])
                path = specmod.config_path(spec, row["run_id"])
                if os.path.isfile(path) and specmod.read_json(path) != cfg:
                    raise StoreError("现有运行配置与模板不同，请使用新 batch_id")
            self.db.execute("INSERT INTO batch_settings VALUES (1, ?)", (signature,))
        added = 0
        for ordinal, job in enumerate(spec["jobs"]):
            row = self.get(job["job_id"])
            if row is None:
                self.db.execute(
                    "INSERT INTO jobs(job_id, ordinal, spec_hash, task, overrides, status, attempt,"
                    " attempt_limit, run_id) VALUES(?,?,?,?,?,'pending',1,?,?)",
                    (job["job_id"], ordinal, job["spec_hash"], json.dumps(job["task"], ensure_ascii=False),
                     json.dumps(job["overrides"], ensure_ascii=False), spec["max_attempts"],
                     specmod.run_id_for(spec, job["job_id"], 1)))
                added += 1
            elif row["spec_hash"] != job["spec_hash"]:
                raise StoreError(f"任务 {job['job_id']} 的定义与已登记的不一致；请改用新的任务编号或新的 batch_id")
        return added

    def start(self, job_id, pid, now):
        self._update(job_id, status="running", pid=pid, started_at=now, finished_at=None,
                     exit_code=None, error=None)

    def _record(self, row, now, exit_code, error):
        history = json.loads(row["history"])
        history.append({"attempt": row["attempt"], "run_id": row["run_id"], "started_at": row["started_at"],
                        "finished_at": now, "exit_code": exit_code, "error": error})
        return json.dumps(history, ensure_ascii=False)

    def complete(self, job_id, now, exit_code):
        row = self.get(job_id)
        self._update(job_id, status="completed", pid=None, finished_at=now, exit_code=exit_code,
                     error=None, history=self._record(row, now, exit_code, None))

    def fail_or_retry(self, spec, job_id, now, exit_code, error):
        """失败后换新 run_id 重试（已失败的运行目录按设计不可重放）；次数用完则终止。"""
        row = self.get(job_id)
        history = self._record(row, now, exit_code, error)
        if row["attempt"] < row["attempt_limit"]:
            attempt = row["attempt"] + 1
            wait = spec["retry_backoff_seconds"] * row["attempt"]
            self._update(job_id, status="pending", pid=None, attempt=attempt,
                         run_id=specmod.run_id_for(spec, job_id, attempt), not_before=now + wait,
                         finished_at=now, exit_code=exit_code, error=error, history=history)
            return "retry"
        self._update(job_id, status="failed", pid=None, finished_at=now, exit_code=exit_code,
                     error=error, history=history)
        return "failed"

    def interrupt(self, job_id):
        """控制器中断：保持同一 run_id，下次直接续跑；不消耗尝试次数。"""
        self._update(job_id, status="pending", pid=None, not_before=0)

    def requeue(self, spec, job_ids):
        changed = []
        for job_id in job_ids:
            row = self.get(job_id)
            if row is None:
                raise StoreError(f"没有这个任务：{job_id}")
            if row["status"] != "failed":
                continue
            attempt = row["attempt"] + 1
            self._update(job_id, status="pending", pid=None, attempt=attempt,
                         attempt_limit=attempt + spec["max_attempts"] - 1,
                         run_id=specmod.run_id_for(spec, job_id, attempt), not_before=0,
                         finished_at=None, exit_code=None, error=None)
            changed.append(job_id)
        return changed


def open_store(spec):
    os.makedirs(spec["batch_dir"], exist_ok=True)
    return Store(os.path.join(spec["batch_dir"], "batch.db"))
