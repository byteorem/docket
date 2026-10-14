import json
from pathlib import Path

from docket import checks, db

NOW = "2026-10-13T00:00:00Z"


def add_task(conn, task_id: str, parent: str | None = None) -> None:
    conn.execute(
        "INSERT INTO tasks (id, parent_id, title, status, created) VALUES (?, ?, 't', 'todo', ?)",
        (task_id, parent, NOW),
    )


def test_clean_store(home):
    conn = db.connect()
    add_task(conn, "a3f8")
    (home / "attachments" / "a3f8").mkdir()
    (home / "attachments" / "a3f8" / "spec.txt").write_text("x")
    conn.execute("INSERT INTO task_links VALUES ('a3f8', 'attachments/a3f8/spec.txt')")
    assert checks.doctor(conn) == {
        "ok": True,
        "integrity": [],
        "foreign_keys": [],
        "orphan_attachment_dirs": [],
        "missing_attachments": [],
    }


def test_foreign_key_violation(home):
    conn = db.connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO task_tags VALUES ('nope', 'x')")
    result = checks.doctor(conn)
    assert not result["ok"]
    assert [(r["table"], r["parent"]) for r in result["foreign_keys"]] == [("task_tags", "tasks")]


def test_orphan_attachment_dir(home):
    conn = db.connect()
    add_task(conn, "a3f8")
    add_task(conn, "a3f8.1", parent="a3f8")
    (home / "attachments" / "a3f8").mkdir()
    (home / "attachments" / "zzzz").mkdir()
    (home / "attachments" / "a3f8.1").mkdir()  # subtasks use the root's folder
    result = checks.doctor(conn)
    assert result["orphan_attachment_dirs"] == ["a3f8.1", "zzzz"]
    assert not result["ok"]


def test_missing_attachment_link(home):
    conn = db.connect()
    add_task(conn, "a3f8")
    conn.execute("INSERT INTO task_links VALUES ('a3f8', 'attachments/a3f8/gone.txt')")
    conn.execute("INSERT INTO task_links VALUES ('a3f8', 'https://example.com/attachments/x')")
    result = checks.doctor(conn)
    assert result["missing_attachments"] == [{"task": "a3f8", "link": "attachments/a3f8/gone.txt"}]
    assert not result["ok"]


def test_cli_clean(home, run):
    result = run("doctor")
    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


def test_cli_findings_json(home, run):
    db.connect().close()
    (Path(home) / "attachments" / "zzzz").mkdir()
    result = run("doctor", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "integrity": [],
        "foreign_keys": [],
        "orphan_attachment_dirs": ["zzzz"],
        "missing_attachments": [],
    }


def test_cli_findings_text(home, run):
    db.connect().close()
    (Path(home) / "attachments" / "zzzz").mkdir()
    result = run("doctor")
    assert result.exit_code == 1
    assert result.stdout.strip() == "orphan attachment folder: zzzz"


def test_doctor_does_not_write(home, run):
    db.connect().close()
    before = (home / "docket.db").stat().st_mtime_ns
    run("doctor")
    assert (home / "docket.db").stat().st_mtime_ns == before
