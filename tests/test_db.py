import sqlite3
from datetime import date, timedelta

import pytest

from docket import db


def table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    return {r[0] for r in rows}


def add_task(conn, tid, parent=None, status="todo", outcome=None, archived=None, title="t"):
    conn.execute(
        "INSERT INTO tasks(id, parent_id, title, status, outcome, archived, created)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, parent, title, status, outcome, archived, db.now()),
    )


def ready_ids(conn):
    return {r[0] for r in conn.execute("SELECT id FROM ready")}


@pytest.fixture
def conn(home):
    c = db.connect()
    yield c
    c.close()


def test_now_format():
    assert db.now().endswith("Z")
    assert len(db.now()) == len("2026-10-11T23:08:00Z")


def test_store_path_env_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKET_HOME", str(tmp_path / "x"))
    assert db.store_path() == tmp_path / "x"
    monkeypatch.delenv("DOCKET_HOME")
    assert db.store_path().name == ".docket"


def test_first_use_creates_store_and_schema(home, conn):
    assert (home / "docket.db").is_file()
    assert (home / "backups").is_dir()
    assert (home / "attachments").is_dir()
    assert {
        "tasks",
        "task_repos",
        "task_tags",
        "task_links",
        "deps",
        "tasks_fts",
        "ready",
    } <= table_names(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert list((home / "backups").iterdir()) == []


def test_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS
    assert conn.row_factory is sqlite3.Row


def test_reconnect_is_idempotent(home, conn):
    add_task(conn, "a1")
    again = db.connect()
    assert again.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    again.close()


def search(conn, term):
    return {r[0] for r in conn.execute("SELECT id FROM tasks_fts WHERE tasks_fts MATCH ?", (term,))}


def test_fts_triggers(conn):
    add_task(conn, "a1", title="alpha")
    conn.execute("UPDATE tasks SET body = 'gamma words' WHERE id = 'a1'")
    assert search(conn, "alpha") == {"a1"}
    assert search(conn, "gamma") == {"a1"}
    conn.execute("UPDATE tasks SET title = 'beta' WHERE id = 'a1'")
    assert search(conn, "alpha") == set()
    assert search(conn, "beta") == {"a1"}
    assert conn.execute("SELECT count(*) FROM tasks_fts").fetchone()[0] == 1
    conn.execute("DELETE FROM tasks WHERE id = 'a1'")
    assert conn.execute("SELECT count(*) FROM tasks_fts").fetchone()[0] == 0


def test_status_update_does_not_duplicate_fts(conn):
    add_task(conn, "a1")
    conn.execute("UPDATE tasks SET status = 'doing' WHERE id = 'a1'")
    assert conn.execute("SELECT count(*) FROM tasks_fts").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("status", "outcome", "archived"),
    [
        ("todo", None, "2026-01-01T00:00:00Z"),  # archived but still has a status
        ("todo", "done", None),  # open but has an outcome
        (None, None, None),  # open without a status
        (None, "done", None),
        (None, None, "2026-01-01T00:00:00Z"),  # archived without an outcome
    ],
)
def test_check_rejects_inconsistent_rows(conn, status, outcome, archived):
    with pytest.raises(sqlite3.IntegrityError):
        add_task(conn, "a1", status=status, outcome=outcome, archived=archived)


def test_check_accepts_archived_row(conn):
    add_task(conn, "a1", status=None, outcome="done", archived="2026-01-01T00:00:00Z")


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_repos VALUES ('missing', 'o/r')")


def test_newer_database_errors(home):
    home.mkdir()
    raw = sqlite3.connect(home / "docket.db")
    raw.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    raw.close()
    with pytest.raises(db.DocketError, match="newer than this CLI"):
        db.connect()


@pytest.mark.parametrize("version", range(len(db.MIGRATIONS)))
def test_migrates_from_every_prior_version(home, version):
    home.mkdir()
    raw = sqlite3.connect(home / "docket.db", isolation_level=None)
    for migration in db.MIGRATIONS[:version]:
        migration(raw)
    raw.execute(f"PRAGMA user_version = {version}")
    raw.close()
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert "tasks" in table_names(conn)
    conn.close()
    backups = sorted(p.name for p in (home / "backups").iterdir())
    if version == 0:
        assert backups == []
    else:
        assert backups == [f"docket-{db._today()}-premigrate-v{version}.db"]


def test_premigrate_backup_when_upgrading_existing_db(home, conn, monkeypatch):
    add_task(conn, "a1")
    conn.close()

    def fake_migration(c):
        c.execute("CREATE TABLE extra (x)")

    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, fake_migration])
    upgraded = db.connect()
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "extra" in table_names(upgraded)
    upgraded.close()

    backup = home / "backups" / f"docket-{db._today()}-premigrate-v1.db"
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 1
    assert old.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert "extra" not in {r[0] for r in old.execute("SELECT name FROM sqlite_master")}
    old.close()


def test_failed_migration_rolls_back(home, conn, monkeypatch):
    conn.close()

    def bad(c):
        c.execute("CREATE TABLE half (x)")
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, bad])
    with pytest.raises(RuntimeError):
        db.connect()
    monkeypatch.undo()
    check = db.connect()
    assert "half" not in table_names(check)
    assert check.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    check.close()


def test_write_commits(home, conn):
    with db.write(conn):
        add_task(conn, "a1")
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_write_rolls_back_and_reraises(conn):
    with pytest.raises(ValueError), db.write(conn):
        add_task(conn, "a1")
        raise ValueError("nope")
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_daily_backup_created_once_per_day(home, conn):
    backup = home / "backups" / f"docket-{db._today()}.db"
    assert not backup.exists()
    with db.write(conn):
        add_task(conn, "a1")
    assert backup.is_file()
    first = backup.stat().st_mtime_ns
    with db.write(conn):
        add_task(conn, "a2")
    assert backup.stat().st_mtime_ns == first
    copy = sqlite3.connect(backup)
    assert copy.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0  # taken before write 1
    copy.close()


def test_retention_keeps_14_most_recent(home, conn, monkeypatch):
    start = date(2026, 1, 1)
    for i in range(20):
        monkeypatch.setattr(db, "_today", lambda i=i: (start + timedelta(days=i)).isoformat())
        with db.write(conn):
            pass
    names = sorted(p.name for p in (home / "backups").iterdir())
    assert len(names) == 14
    assert names[0] == "docket-2026-01-07.db"
    assert names[-1] == "docket-2026-01-20.db"


def test_retention_covers_premigrate_backups(home, conn):
    backups = home / "backups"
    for i in range(1, 16):
        (backups / f"docket-2026-01-{i:02d}.db").write_bytes(b"")
    (backups / "docket-2026-01-15-premigrate-v1.db").write_bytes(b"")
    (backups / "unrelated.txt").write_text("keep")
    with db.write(conn):
        pass
    names = sorted(p.name for p in backups.iterdir())
    assert "unrelated.txt" in names
    assert len([n for n in names if n.startswith("docket-")]) == 14
    assert "docket-2026-01-01.db" not in names


def test_busy_database_maps_to_docket_error(home, monkeypatch):
    monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 50)
    conn = db.connect()
    other = sqlite3.connect(home / "docket.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(db.DocketError, match="database busy"), db.write(conn):
            pass
    finally:
        other.execute("ROLLBACK")
        other.close()
        conn.close()


def test_known_repos(conn):
    add_task(conn, "a1")
    add_task(conn, "a2")
    for tid, r in [("a1", "z/z"), ("a1", "b/b"), ("a2", "b/b")]:
        conn.execute("INSERT INTO task_repos VALUES (?, ?)", (tid, r))
    assert db.known_repos(conn) == ["b/b", "z/z"]


def test_ready_view(conn):
    done = "2026-01-01T00:00:00Z"
    add_task(conn, "dep")
    add_task(conn, "blocked-by-open")
    conn.execute("INSERT INTO deps VALUES ('blocked-by-open', 'dep')")
    add_task(conn, "done-dep", status=None, outcome="done", archived=done)
    add_task(conn, "unblocked")
    conn.execute("INSERT INTO deps VALUES ('unblocked', 'done-dep')")
    add_task(conn, "canceled-dep", status=None, outcome="canceled", archived=done)
    add_task(conn, "blocked-by-canceled")
    conn.execute("INSERT INTO deps VALUES ('blocked-by-canceled', 'canceled-dep')")
    add_task(conn, "parent")
    add_task(conn, "parent.1", parent="parent")
    add_task(conn, "doing", status="doing")
    add_task(conn, "anc")
    conn.execute("INSERT INTO deps VALUES ('anc', 'dep')")
    add_task(conn, "anc.1", parent="anc")
    add_task(conn, "anc.1.1", parent="anc.1")

    assert ready_ids(conn) == {"dep", "unblocked", "parent.1"}

    conn.execute(
        "UPDATE tasks SET status = NULL, outcome = 'done', archived = ? WHERE id = 'dep'", (done,)
    )
    assert ready_ids(conn) == {"blocked-by-open", "unblocked", "parent.1", "anc.1.1"}
