"""Shared helpers for the task command tests."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docket import db


def mk(run: Callable[..., Any], cwd: Path, title: str, *args: str) -> str:
    """Add a task (outside any repo unless args say otherwise) and return its ID."""
    result = run("add", title, "--json", *args, cwd=cwd)
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)["id"]


def js(run: Callable[..., Any], cwd: Path, *args: str) -> Any:
    result = run(*args, "--json", cwd=cwd)
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def ok(run: Callable[..., Any], cwd: Path, *args: str) -> str:
    result = run(*args, cwd=cwd)
    assert result.exit_code == 0, result.stderr
    return result.stdout


def fail(run: Callable[..., Any], cwd: Path, *args: str) -> str:
    """Run a command expected to fail with exit 1; return stderr. --json stdout stays empty."""
    result = run(*args, "--json", cwd=cwd)
    assert result.exit_code == 1, (result.exit_code, result.stdout)
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    return result.stderr


def set_created(task_id: str, created: str) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE tasks SET created = ? WHERE id = ?", (created, task_id))
    finally:
        conn.close()
