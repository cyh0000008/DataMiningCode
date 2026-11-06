"""Durable, origin-independent report delivery; no Qt or CLI dependency in miners."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import secrets
import time
import zipfile
from pathlib import Path

from .paths import user_data_dir


def _config_path(path: Path | None) -> Path:
    return path if path is not None else user_data_dir() / "feishu_bot.json"


def _binding(path: Path) -> dict:
    if os.environ.get("UNIFIED_MINING_DISABLE_PUSH") == "1" or not path.is_file():
        return {}
    config = json.loads(path.read_text(encoding="utf-8"))
    if not config.get("enabled") or not config.get("owner_open_id") or not config.get("chat_id"):
        return {}
    return config


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path.with_suffix(".outbox.sqlite3")), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
        created REAL NOT NULL, updated REAL NOT NULL, next_try REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')""")
    db.commit()
    return db


def enqueue_bundle(bundle: Path, title: str = "", job_id: str = "", *,
                   path: Path | None = None, resend: bool = False) -> str | None:
    path = _config_path(path)
    binding = _binding(path)
    if not binding:
        return None
    bundle = Path(bundle).resolve(strict=True)
    if bundle.suffix.lower() != ".zip" or not zipfile.is_zipfile(bundle):
        raise ValueError("回传文件必须是完整的 ZIP")
    stat = bundle.stat()
    key = hashlib.sha256(json.dumps([str(bundle), stat.st_mtime_ns, stat.st_size,
                                   binding["owner_open_id"], binding["chat_id"]]).encode()).hexdigest()[:40]
    if resend:
        key = secrets.token_hex(20)
    payload = {"bundle": str(bundle), "title": title, "job_id": job_id,
               "owner_open_id": binding["owner_open_id"], "chat_id": binding["chat_id"]}
    db = _connect(path)
    try:
        now = time.time()
        db.execute("INSERT OR IGNORE INTO deliveries (id,payload,status,created,updated,next_try) "
                   "VALUES (?,?,'pending',?,?,?)", (key, json.dumps(payload, ensure_ascii=False), now, now, now))
        db.commit()
    finally:
        db.close()
    return key


def claim_delivery(path: Path) -> dict | None:
    binding = _binding(path)
    if not binding:
        return None
    db = _connect(path)
    try:
        now = time.time()
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT * FROM deliveries WHERE (status='pending' AND next_try<=?) "
                          "OR (status='sending' AND updated<?) ORDER BY created LIMIT 20",
                          (now, now - 600)).fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if (payload["owner_open_id"] != binding["owner_open_id"]
                    or payload["chat_id"] != binding["chat_id"]):
                db.execute("UPDATE deliveries SET status='blocked',error='binding changed' WHERE id=?", (row["id"],))
                continue
            db.execute("UPDATE deliveries SET status='sending',updated=?,attempts=attempts+1 WHERE id=?",
                       (now, row["id"]))
            db.commit()
            return {**payload, "outbox_id": row["id"]}
        db.commit()
        return None
    finally:
        db.close()


def finish_delivery(path: Path, key: str, success: bool) -> None:
    db = _connect(path)
    try:
        row = db.execute("SELECT attempts FROM deliveries WHERE id=?", (key,)).fetchone()
        if row is None:
            return
        now = time.time()
        delay = min(3600, 60 * 2 ** min(int(row["attempts"]), 6))
        db.execute("UPDATE deliveries SET status=?,updated=?,next_try=?,error=? WHERE id=?",
                   ("sent" if success else "pending", now, now + delay,
                    "" if success else "send failed; retry scheduled", key))
        db.commit()
    finally:
        db.close()


def enqueue_mining_results(kind: str, report: Path, bad_report: Path | None = None) -> str | None:
    """Called only by a successful standalone miner. Managed jobs wait for comparison."""
    if os.environ.get("UNIFIED_MINING_MANAGED") == "1":
        return None
    path = _config_path(None)
    if not _binding(path):
        return None
    report = Path(report).resolve(strict=True)
    reports = [report]
    if bad_report is not None:
        bad_report = Path(bad_report).resolve(strict=True)
        if bad_report != report:
            reports.append(bad_report)
    for item in reports:
        if item.suffix.lower() != ".xlsx":
            raise ValueError("仅回传本次生成的 XLSX，不包含原始 H5")
    identity = hashlib.sha256(json.dumps([[str(p), p.stat().st_mtime_ns, p.stat().st_size]
                                         for p in reports]).encode()).hexdigest()[:12]
    bundle = report.parent / f"{report.stem}-{kind}-results-{identity}.zip"
    if not bundle.is_file():
        temporary = bundle.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in reports:
                    archive.write(item, arcname=item.name)
            temporary.replace(bundle)
        finally:
            temporary.unlink(missing_ok=True)
    return enqueue_bundle(bundle, title=kind.upper() + " 挖掘结果", path=path)
