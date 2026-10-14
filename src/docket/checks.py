"""Store health checks. Returns plain data; `cli.py` formats it."""

from __future__ import annotations

import sqlite3

from docket import db


def doctor(conn: sqlite3.Connection) -> dict:
    """Read-only checks: SQLite integrity, foreign keys, and attachment consistency."""
    integrity = [r[0] for r in conn.execute("PRAGMA integrity_check") if r[0] != "ok"]
    foreign_keys = [
        {"table": r[0], "rowid": r[1], "parent": r[2], "fkid": r[3]}
        for r in conn.execute("PRAGMA foreign_key_check")
    ]

    store = db.store_path()
    roots = {r[0] for r in conn.execute("SELECT id FROM tasks WHERE parent_id IS NULL")}
    attachments = store / "attachments"
    orphan_dirs = sorted(
        p.name for p in attachments.iterdir() if p.is_dir() and p.name not in roots
    )

    missing = [
        {"task": r[0], "link": r[1]}
        for r in conn.execute(
            "SELECT task_id, link FROM task_links WHERE link LIKE 'attachments/%' "
            "ORDER BY task_id, link"
        )
        if not (store / r[1]).exists()
    ]

    return {
        "ok": not (integrity or foreign_keys or orphan_dirs or missing),
        "integrity": integrity,
        "foreign_keys": foreign_keys,
        "orphan_attachment_dirs": orphan_dirs,
        "missing_attachments": missing,
    }
