"""SQLite 只保存数据集和任务状态；原始上传另存为只读用途的副本。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .tables import Table
from .makeups import MakeupRequest, apply_makeup


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / "records.sqlite3"
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS makeup_operations (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, request TEXT NOT NULL, run_id TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.db, timeout=15)

    def save_dataset(self, registration: Table, checkin: Table, originals: tuple[bytes, bytes]):
        dataset_id = uuid.uuid4().hex
        folder = self.root / "uploads" / dataset_id
        folder.mkdir(parents=True)
        (folder / "registration.csv").write_bytes(originals[0])
        (folder / "checkin.csv").write_bytes(originals[1])
        payload = {"id": dataset_id, "created_at": now(), "registration": registration.to_dict(), "checkin": checkin.to_dict()}
        with self.connect() as conn:
            conn.execute("INSERT INTO datasets VALUES (?, ?)", (dataset_id, json.dumps(payload, ensure_ascii=False)))
        return dataset_id

    def dataset(self, dataset_id: str):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise KeyError("数据集不存在，请重新导入。")
        data = json.loads(row[0])
        return Table(**data["registration"]), Table(**data["checkin"])

    def create_run(self, dataset_id: str, mode: str, task: str = ""):
        run = {"id": uuid.uuid4().hex, "dataset_id": dataset_id, "mode": mode,
               "task": task, "status": "running", "created_at": now(), "updated_at": now(),
               "result": None, "trace": [], "messages": [], "question": None,
               "error": None, "rounds": 0, "usage": {"prompt_tokens": 0, "completion_tokens": 0},
               "requests": 0}
        self.save_run(run)
        return run

    def save_run(self, run: dict):
        run["updated_at"] = now()
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)", (run["id"], json.dumps(run, ensure_ascii=False)))

    def run(self, run_id: str):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("任务不存在。")
        return json.loads(row[0])

    def recent_runs(self):
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM runs ORDER BY rowid DESC LIMIT 15").fetchall()
        return [{k: r[k] for k in ("id", "mode", "status", "created_at", "task")} for r in (json.loads(row[0]) for row in rows)]

    def makeup(self, run_id: str, request: MakeupRequest):
        """使用事务保存派生快照；同一 operation_id 重试只产生一个结果。"""
        request_json = json.dumps(request.model_dump(), sort_keys=True, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT source_id, request, run_id FROM makeup_operations WHERE id=?", (request.operation_id,)).fetchone()
            if previous:
                if previous[0] != run_id or previous[1] != request_json:
                    raise ValueError("该补签操作编号已用于其他内容，请重新选择人员后提交。")
                row = conn.execute("SELECT payload FROM runs WHERE id=?", (previous[2],)).fetchone()
                return json.loads(row[0])
            row = conn.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("任务不存在。")
            source = json.loads(row[0])
            if source["status"] != "completed" or not source.get("result"):
                raise ValueError("只有已完成的核对结果可以补签。")
            stamp = now()
            result = apply_makeup(source["result"], request, stamp)
            derived = {"id": uuid.uuid4().hex, "dataset_id": source["dataset_id"], "mode": "local",
                       "task": "人工补签：" + " / ".join(request.key), "status": "completed",
                       "created_at": stamp, "updated_at": stamp, "result": result, "messages": [],
                       "question": None, "error": None, "rounds": 0, "requests": 0,
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                       "parent_run_id": source["id"], "origin_mode": source.get("origin_mode", source["mode"]),
                       "trace": [{"time": stamp, "step": "继承核对结果", "status": "ok", "duration_ms": 0,
                                  "detail": "保留原始文件和补签前快照：" + source["id"]},
                                 {"time": stamp, "step": "人工补签", "status": "ok", "duration_ms": 0,
                                  "detail": "已保存实际签到时间、补录时间和原因；统计及导出已更新。"}]}
            conn.execute("INSERT INTO runs VALUES (?,?)", (derived["id"], json.dumps(derived, ensure_ascii=False)))
            conn.execute("INSERT INTO makeup_operations VALUES (?,?,?,?)", (request.operation_id, run_id, request_json, derived["id"]))
        return derived

    def recover_interrupted(self):
        """仅在单进程服务启动时调用；不把上次进程中的任务永远显示为执行中。"""
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM runs").fetchall()
        for row in rows:
            run = json.loads(row[0])
            if run["status"] == "running":
                run["status"] = "failed"
                run["error"] = "上次服务退出时任务未完成，请新建任务重新核对。"
                # 中断可能留下没有工具回执的历史，不能自动续接这段历史。
                run["interrupted"] = True
                self.save_run(run)


def public_run(run):
    data = {k: v for k, v in run.items() if k != "messages"}
    if run["result"]:
        result = dict(run["result"])
        result["preview_counts"] = {k: len(result.get(k, [])) for k in ("details", "issues", "groups", "makeups")}
        for field in ("details", "issues", "groups", "makeups"):
            result[field] = result.get(field, [])[:200]
        data["result"] = result
    return data
