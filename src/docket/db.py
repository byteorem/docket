"""Connection, schema, migrations, and backups."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

BUSY_TIMEOUT_MS = 10000
BACKUP_KEEP = 14

_BACKUP_RE = re.compile(r"^docket-(\d{4}-\d{2}-\d{2})(?:-premigrate-v\d+)?\.db$")


class DocketError(Exception):
    """A command error. The message is the one line shown to the user."""


def store_path() -> Path:
    env = os.environ.get("DOCKET_HOME")
    return Path(env) if env else Path.home() / ".docket"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


_SCHEMA_V1 = [
    """
CREATE TABLE tasks (
  id              TEXT PRIMARY KEY,
  parent_id       TEXT REFERENCES tasks(id),
  title           TEXT NOT NULL,
  body            TEXT NOT NULL DEFAULT '',
  status          TEXT CHECK (status IN ('todo', 'doing', 'review', 'blocked')),
  blocked_on      TEXT,
  discovered_from TEXT REFERENCES tasks(id),
  branch          TEXT,
  pr              TEXT,
  issue           TEXT,
  created         TEXT NOT NULL,
  outcome         TEXT CHECK (outcome IN ('done', 'canceled')),
  archived        TEXT,
  CHECK ((archived IS NULL     AND status IS NOT NULL AND outcome IS NULL)
      OR (archived IS NOT NULL AND status IS NULL     AND outcome IS NOT NULL))
)""",
    """
CREATE TABLE task_repos (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  repo    TEXT NOT NULL,
  PRIMARY KEY (task_id, repo)
)""",
    """
CREATE TABLE task_tags (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  tag     TEXT NOT NULL,
  PRIMARY KEY (task_id, tag)
)""",
    """
CREATE TABLE task_links (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  link    TEXT NOT NULL,
  PRIMARY KEY (task_id, link)
)""",
    """
CREATE TABLE deps (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  dep_id  TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, dep_id)
)""",
    "CREATE INDEX idx_tasks_parent ON tasks(parent_id)",
    "CREATE INDEX idx_deps_dep ON deps(dep_id)",
    "CREATE INDEX idx_task_repos_repo ON task_repos(repo)",
    "CREATE INDEX idx_task_tags_tag ON task_tags(tag)",
    "CREATE VIRTUAL TABLE tasks_fts USING fts5(id UNINDEXED, title, body)",
    """
CREATE TRIGGER tasks_fts_insert AFTER INSERT ON tasks BEGIN
  INSERT INTO tasks_fts(id, title, body) VALUES (new.id, new.title, new.body);
END""",
    """
CREATE TRIGGER tasks_fts_update AFTER UPDATE OF id, title, body ON tasks BEGIN
  DELETE FROM tasks_fts WHERE id = old.id;
  INSERT INTO tasks_fts(id, title, body) VALUES (new.id, new.title, new.body);
END""",
    """
CREATE TRIGGER tasks_fts_delete AFTER DELETE ON tasks BEGIN
  DELETE FROM tasks_fts WHERE id = old.id;
END""",
    """
CREATE VIEW ready AS
WITH RECURSIVE lineage(task_id, ancestor_id) AS (
  SELECT id, id FROM tasks WHERE archived IS NULL
  UNION ALL
  SELECT l.task_id, t.parent_id
  FROM lineage l JOIN tasks t ON t.id = l.ancestor_id
  WHERE t.parent_id IS NOT NULL
)
SELECT t.* FROM tasks t
WHERE t.archived IS NULL
  AND t.status = 'todo'
  AND NOT EXISTS (
    SELECT 1 FROM lineage l
    JOIN deps d  ON d.task_id = l.ancestor_id
    JOIN tasks x ON x.id = d.dep_id
    WHERE l.task_id = t.id
      AND (x.archived IS NULL OR x.outcome <> 'done'))
  AND NOT EXISTS (
    SELECT 1 FROM tasks c WHERE c.parent_id = t.id AND c.archived IS NULL)""",
]


def _migrate_1(conn: sqlite3.Connection) -> None:
    for stmt in _SCHEMA_V1:
        conn.execute(stmt)


# Index i migrates from version i to i+1.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_1]


def _prune_backups(backups: Path) -> None:
    found = []
    for p in backups.iterdir():
        m = _BACKUP_RE.match(p.name)
        if m:
            found.append(((m.group(1), p.name), p))
    found.sort(key=lambda item: item[0])
    for _, p in found[:-BACKUP_KEEP]:
        p.unlink(missing_ok=True)


def _backup_to(conn: sqlite3.Connection, dest: Path) -> None:
    """Copy to a temp file, then atomically move it onto `dest`."""
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    out = sqlite3.connect(tmp)
    try:
        try:
            conn.backup(out)
        finally:
            out.close()
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _check_fts5(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts5_probe")
    except sqlite3.OperationalError as e:
        raise DocketError(
            "this Python's SQLite was built without FTS5, which Docket requires"
        ) from e


def _is_busy(e: sqlite3.OperationalError) -> bool:
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg


def _migrate(conn: sqlite3.Connection, backups: Path) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise DocketError(
            f"database is version {version}, newer than this CLI (supports {len(MIGRATIONS)}); "
            "upgrade docket"
        )
    if version == len(MIGRATIONS):
        return
    try:
        if version > 0:
            _backup_to(conn, backups / f"docket-{_today()}-premigrate-v{version}.db")
            _prune_backups(backups)
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-read: another process may have migrated while we waited for the lock.
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for i in range(version, len(MIGRATIONS)):
                MIGRATIONS[i](conn)
            conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.OperationalError as e:
        if _is_busy(e):
            raise DocketError("database busy") from e
        raise


def connect() -> sqlite3.Connection:
    root = store_path()
    (root / "backups").mkdir(parents=True, exist_ok=True)
    (root / "attachments").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "docket.db", isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        _check_fts5(conn)
        _migrate(conn, root / "backups")
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def write(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """The only way to write: daily backup, then one BEGIN IMMEDIATE transaction."""
    try:
        backups = store_path() / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        daily = backups / f"docket-{_today()}.db"
        if not daily.exists():
            _backup_to(conn, daily)
            _prune_backups(backups)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.OperationalError as e:
        if _is_busy(e):
            raise DocketError("database busy") from e
        raise


def known_repos(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT repo FROM task_repos ORDER BY repo").fetchall()
    return [r[0] for r in rows]
