"""Store health checks. Returns plain data; `cli.py` formats it."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from docket import db, tasks


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


def _md(task: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in task.items():
        if key == "body":
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        lines.append(f"{key}: {'' if shown is None else shown}")
    return "\n".join([*lines, "---", "", task["body"]])


def export(conn: sqlite3.Connection, fmt: str, out: Path | None) -> dict[str, Any]:
    """Read-only dump of every task. `tasks` is the list; `path` is set when files were written."""
    if fmt not in ("jsonl", "md"):
        raise db.DocketError(f"unknown format '{fmt}'; use jsonl or md")
    all_tasks = tasks.get_all(conn)
    if out is None:
        if fmt == "md":
            raise db.DocketError("md export needs --out")
        return {"format": fmt, "path": None, "count": len(all_tasks), "tasks": all_tasks}
    try:
        out.mkdir(parents=True, exist_ok=True)
        if fmt == "jsonl":
            path = out / "docket.jsonl"
            lines = [json.dumps(t, ensure_ascii=False) + "\n" for t in all_tasks]
            path.write_bytes("".join(lines).encode("utf-8"))
        else:
            path = out
            for t in all_tasks:
                (out / f"{t['id']}.md").write_bytes(_md(t).encode("utf-8"))
    except OSError as e:
        raise db.DocketError(f"cannot write to {out}: {e.strerror or e}") from e
    return {"format": fmt, "path": str(path), "count": len(all_tasks), "tasks": all_tasks}
