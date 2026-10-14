"""Task logic: lifecycle, relationships, cycle rule, readiness, search. Returns plain data."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from docket import db
from docket import repo as repo_mod
from docket.db import DocketError
from docket.repo import Scope

STATUSES = ("todo", "doing", "review", "blocked")
SCALAR_FIELDS = ("title", "status", "blocked_on", "branch", "pr", "issue")
# Scalar fields that make no sense on an archived task.
OPEN_ONLY_FIELDS = ("status", "blocked_on")
LIST_FIELDS = {
    "tags": ("task_tags", "tag"),
    "repos": ("task_repos", "repo"),
    "links": ("task_links", "link"),
}

_ID_ALPHABET = string.ascii_lowercase + string.digits

_SELECT = """
SELECT t.id, t.parent_id, t.title, t.body, t.status, t.blocked_on, t.discovered_from,
       t.branch, t.pr, t.issue, t.created, t.outcome, t.archived,
       (SELECT json_group_array(repo) FROM task_repos WHERE task_id = t.id) AS repos,
       (SELECT json_group_array(tag) FROM task_tags WHERE task_id = t.id) AS tags,
       (SELECT json_group_array(link) FROM task_links WHERE task_id = t.id) AS links,
       (SELECT json_group_array(dep_id) FROM deps WHERE task_id = t.id) AS depends_on,
       {ready} AS ready
"""


def _entry(row: sqlite3.Row, with_ready: bool) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": row["id"],
        "parent": row["parent_id"],
        "title": row["title"],
        "body": row["body"],
        "status": row["status"],
        "blocked_on": row["blocked_on"],
        "repos": sorted(json.loads(row["repos"])),
        "depends_on": sorted(json.loads(row["depends_on"])),
        "discovered_from": row["discovered_from"],
        "tags": sorted(json.loads(row["tags"])),
        "branch": row["branch"],
        "pr": row["pr"],
        "issue": row["issue"],
        "links": sorted(json.loads(row["links"])),
        "created": row["created"],
        "outcome": row["outcome"],
        "archived": row["archived"],
    }
    if with_ready:
        task["ready"] = bool(row["ready"])
    return task


def _select(
    conn: sqlite3.Connection,
    where: str = "1",
    params: Sequence[Any] = (),
    *,
    frm: str = "tasks t",
    order: str = "t.created, t.id",
    with_ready: bool = False,
) -> list[dict[str, Any]]:
    ready = "t.id IN (SELECT id FROM ready)" if with_ready else "0"
    sql = f"{_SELECT.format(ready=ready)} FROM {frm} WHERE {where} ORDER BY {order}"
    return [_entry(r, with_ready) for r in conn.execute(sql, list(params))]


def get(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """The task with this exact ID."""
    rows = _select(conn, "t.id = ?", [task_id])
    if not rows:
        raise DocketError(f"no task '{task_id}'")
    return rows[0]


def get_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every task, sorted by created then id."""
    return _select(conn)


def resolve_id(conn: sqlite3.Connection, ref: str) -> str:
    """Exact ID, else unique prefix. Global: not filtered by scope."""
    ref = ref.strip().lower()
    if not ref:
        raise DocketError("empty task ID")
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (ref,)).fetchone():
        return ref
    rows = conn.execute(
        "SELECT id FROM tasks WHERE substr(id, 1, ?) = ? ORDER BY id", (len(ref), ref)
    ).fetchall()
    if not rows:
        raise DocketError(f"no task matches '{ref}'")
    if len(rows) > 1:
        raise DocketError(f"'{ref}' is ambiguous: {', '.join(r[0] for r in rows)}")
    return rows[0][0]


def _require_open(conn: sqlite3.Connection, task_id: str, what: str) -> None:
    row = conn.execute("SELECT archived FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row["archived"] is not None:
        raise DocketError(f"{task_id} is archived; {what}")


def _scope_sql(scope: Scope, *, with_unscoped: bool) -> tuple[str, list[str]]:
    """`with_unscoped` also admits tasks that have no repos (ls, search, index)."""
    if scope.all_repos:
        return "1", []
    no_repo = "NOT EXISTS (SELECT 1 FROM task_repos r WHERE r.task_id = t.id)"
    if scope.key is None:
        return no_repo, []
    in_repo = "EXISTS (SELECT 1 FROM task_repos r WHERE r.task_id = t.id AND r.repo = ?)"
    if with_unscoped:
        return f"({in_repo} OR {no_repo})", [scope.key]
    return in_repo, [scope.key]


# --- cycle rule ---

_REACH_SQL = """
WITH RECURSIVE
lineage(task_id, anc) AS (
  SELECT id, parent_id FROM tasks WHERE parent_id IS NOT NULL AND archived IS NULL
  UNION
  SELECT l.task_id, t.parent_id FROM lineage l JOIN tasks t ON t.id = l.anc
  WHERE t.parent_id IS NOT NULL
),
edges(src, dst) AS (
  SELECT d.task_id, d.dep_id FROM deps d
    JOIN tasks a ON a.id = d.task_id JOIN tasks b ON b.id = d.dep_id
    WHERE a.archived IS NULL AND b.archived IS NULL
  UNION
  SELECT l.task_id, d.dep_id FROM lineage l
    JOIN deps d ON d.task_id = l.anc JOIN tasks b ON b.id = d.dep_id
    WHERE b.archived IS NULL
  UNION
  SELECT parent_id, id FROM tasks WHERE parent_id IS NOT NULL AND archived IS NULL
),
reach(node) AS (
  SELECT dst FROM edges WHERE src = ?
  UNION
  SELECT e.dst FROM edges e JOIN reach r ON e.src = r.node
)
SELECT node FROM reach
"""


def _reachable(conn: sqlite3.Connection, start: str) -> set[str]:
    """Open tasks reachable from `start` in one or more steps, over the open-task graph."""
    return {r[0] for r in conn.execute(_REACH_SQL, (start,))}


def _check_dep_cycle(conn: sqlite3.Connection, task_id: str, dep_id: str) -> None:
    descendants = conn.execute(
        "SELECT id FROM tasks WHERE substr(id, 1, ?) = ?", (len(task_id) + 1, task_id + ".")
    ).fetchall()
    targets = {task_id, *(r[0] for r in descendants)}
    if ({dep_id} | _reachable(conn, dep_id)) & targets:
        raise DocketError(f"dependency {task_id} -> {dep_id} would create a cycle")


def _check_reopen_cycle(conn: sqlite3.Connection, task_id: str) -> None:
    if task_id in _reachable(conn, task_id):
        raise DocketError(f"reopening {task_id} would create a dependency cycle")


# --- writes ---


def _add_dep(conn: sqlite3.Connection, task_id: str, dep_ref: str) -> None:
    dep_id = resolve_id(conn, dep_ref)
    _require_open(conn, dep_id, "it cannot be a dependency")
    conn.execute("INSERT OR IGNORE INTO deps(task_id, dep_id) VALUES (?, ?)", (task_id, dep_id))
    _check_dep_cycle(conn, task_id, dep_id)


def _new_top_id(conn: sqlite3.Connection) -> str:
    while True:
        candidate = "".join(secrets.choice(_ID_ALPHABET) for _ in range(4))
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (candidate,)).fetchone():
            return candidate


def _new_child_id(conn: sqlite3.Connection, parent_id: str) -> str:
    numbers = [
        int(r[0].rsplit(".", 1)[1])
        for r in conn.execute("SELECT id FROM tasks WHERE parent_id = ?", (parent_id,))
    ]
    return f"{parent_id}.{max(numbers, default=0) + 1}"


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def add(
    conn: sqlite3.Connection,
    title: str,
    *,
    body: str = "",
    parent: str | None = None,
    deps: Sequence[str] = (),
    tags: Sequence[str] = (),
    repos: Sequence[str] = (),
    discovered_from: str | None = None,
) -> dict[str, Any]:
    """Create a task. `repos` are final repo keys."""
    title = title.strip()
    if not title:
        raise DocketError("title is empty")
    with db.write(conn):
        parent_id = None
        if parent:
            parent_id = resolve_id(conn, parent)
            _require_open(conn, parent_id, "a subtask needs an open parent")
        origin = resolve_id(conn, discovered_from) if discovered_from else None
        task_id = _new_child_id(conn, parent_id) if parent_id else _new_top_id(conn)
        conn.execute(
            "INSERT INTO tasks(id, parent_id, title, body, status, discovered_from, created) "
            "VALUES (?, ?, ?, ?, 'todo', ?, ?)",
            (task_id, parent_id, title, body, origin, db.now()),
        )
        for table, column, values in (
            ("task_repos", "repo", repos),
            ("task_tags", "tag", tags),
        ):
            conn.executemany(
                f"INSERT INTO {table}(task_id, {column}) VALUES (?, ?)",
                [(task_id, v) for v in _unique(values)],
            )
        for dep_ref in deps:
            _add_dep(conn, task_id, dep_ref)
        return get(conn, task_id)


def dep(conn: sqlite3.Connection, ref: str, dep_ref: str) -> dict[str, Any]:
    with db.write(conn):
        task_id = resolve_id(conn, ref)
        _add_dep(conn, task_id, dep_ref)
        return get(conn, task_id)


def undep(conn: sqlite3.Connection, ref: str, dep_ref: str) -> dict[str, Any]:
    with db.write(conn):
        task_id = resolve_id(conn, ref)
        dep_id = resolve_id(conn, dep_ref)
        cur = conn.execute("DELETE FROM deps WHERE task_id = ? AND dep_id = ?", (task_id, dep_id))
        if cur.rowcount == 0:
            raise DocketError(f"{task_id} does not depend on {dep_id}")
        return get(conn, task_id)


def _split_values(value: str) -> list[str]:
    return _unique(value.split(","))


def _set_list(
    conn: sqlite3.Connection,
    task_id: str,
    field: str,
    value: str | None,
    add_values: Sequence[str],
    remove_values: Sequence[str],
) -> None:
    table, column = LIST_FIELDS[field]

    def norm(values: Iterable[str]) -> list[str]:
        vals = _unique(values)
        return [repo_mod.resolve_key(conn, v) for v in vals] if field == "repos" else vals

    current = {
        r[0] for r in conn.execute(f"SELECT {column} FROM {table} WHERE task_id = ?", (task_id,))
    }
    new = set(current) if value is None else set(norm(_split_values(value)))
    new |= set(norm(add_values))
    new -= set(norm(remove_values))
    conn.executemany(
        f"DELETE FROM {table} WHERE task_id = ? AND {column} = ?",
        [(task_id, v) for v in current - new],
    )
    conn.executemany(
        f"INSERT INTO {table}(task_id, {column}) VALUES (?, ?)",
        [(task_id, v) for v in new - current],
    )


def _set_scalar(conn: sqlite3.Connection, task: dict[str, Any], field: str, value: str) -> None:
    task_id = task["id"]
    if field in OPEN_ONLY_FIELDS and task["archived"] is not None:
        raise DocketError(f"{task_id} is archived; cannot set {field}")
    if field == "title":
        if not value.strip():
            raise DocketError("title is empty")
        conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (value.strip(), task_id))
    elif field == "status":
        if value not in STATUSES:
            raise DocketError(f"invalid status '{value}'; use {', '.join(STATUSES)}")
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (value, task_id))
        if value != "blocked":
            conn.execute("UPDATE tasks SET blocked_on = NULL WHERE id = ?", (task_id,))
    elif field == "blocked_on":
        if task["status"] != "blocked":
            raise DocketError(f"{task_id} is not blocked; set status blocked first")
        conn.execute("UPDATE tasks SET blocked_on = ? WHERE id = ?", (value or None, task_id))
    else:
        conn.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (value or None, task_id))


def set_field(
    conn: sqlite3.Connection,
    ref: str,
    field: str,
    value: str | None = None,
    add_values: Sequence[str] = (),
    remove_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Scalar fields need `value`; list fields take `value` (replace) and/or add/remove."""
    with db.write(conn):
        task = get(conn, resolve_id(conn, ref))
        if field in LIST_FIELDS:
            _set_list(conn, task["id"], field, value, add_values, remove_values)
        elif field in SCALAR_FIELDS:
            if value is None or add_values or remove_values:
                raise DocketError(f"{field} takes a single value")
            _set_scalar(conn, task, field, value)
        else:
            raise DocketError(f"unknown field '{field}'")
        return get(conn, task["id"])


def note(conn: sqlite3.Connection, ref: str, text: str) -> dict[str, Any]:
    with db.write(conn):
        task = get(conn, resolve_id(conn, ref))
        body = task["body"]
        if body and not body.endswith("\n"):
            body += "\n"
        body += f"- {db.now()} {text}"
        conn.execute("UPDATE tasks SET body = ? WHERE id = ?", (body, task["id"]))
        return get(conn, task["id"])


def _archive(conn: sqlite3.Connection, ref: str, outcome: str) -> dict[str, Any]:
    with db.write(conn):
        task_id = resolve_id(conn, ref)
        _require_open(conn, task_id, f"it cannot be {outcome}")
        children = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM tasks WHERE parent_id = ? AND archived IS NULL ORDER BY id",
                (task_id,),
            )
        ]
        if children:
            raise DocketError(f"{task_id} has open subtasks: {', '.join(children)}")
        if outcome == "canceled":
            dependents = [
                r[0]
                for r in conn.execute(
                    "SELECT d.task_id FROM deps d JOIN tasks x ON x.id = d.task_id "
                    "WHERE d.dep_id = ? AND x.archived IS NULL ORDER BY d.task_id",
                    (task_id,),
                )
            ]
            if dependents:
                raise DocketError(
                    f"{task_id} cannot be canceled; open tasks depend on it: "
                    f"{', '.join(dependents)}"
                )
        conn.execute(
            "UPDATE tasks SET status = NULL, blocked_on = NULL, outcome = ?, archived = ? "
            "WHERE id = ?",
            (outcome, db.now(), task_id),
        )
        return get(conn, task_id)


def complete(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    return _archive(conn, ref, "done")


def cancel(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    return _archive(conn, ref, "canceled")


def reopen(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    with db.write(conn):
        task = get(conn, resolve_id(conn, ref))
        task_id = task["id"]
        if task["archived"] is None:
            raise DocketError(f"{task_id} is not archived")
        if task["parent"]:
            parent = get(conn, task["parent"])
            if parent["archived"] is not None:
                raise DocketError(f"cannot reopen {task_id}: parent {parent['id']} is archived")
        conn.execute(
            "UPDATE tasks SET status = 'doing', outcome = NULL, archived = NULL WHERE id = ?",
            (task_id,),
        )
        _check_reopen_cycle(conn, task_id)
        return get(conn, task_id)


# --- body: edit, attach ---


def _editor_argv(value: str) -> list[str]:
    whole = shutil.which(value)
    if whole:
        return [whole]
    tokens = shlex.split(value, posix=os.name != "nt")
    tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]
    if not tokens:
        raise DocketError("editor command is empty")
    program = shutil.which(tokens[0])
    if program is None:
        raise DocketError(f"editor not found: {tokens[0]}")
    return [program, *tokens[1:]]


def edit(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    """Edit the body in $VISUAL or $EDITOR; save only if it changed."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        raise DocketError("set $VISUAL or $EDITOR to edit a task")
    argv = _editor_argv(editor)
    task = get(conn, resolve_id(conn, ref))
    fd, name = tempfile.mkstemp(suffix=".md", prefix=f"docket-{task['id']}-")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(task["body"].encode("utf-8"))
        try:
            proc = subprocess.run([*argv, name], check=False)
        except OSError as e:
            raise DocketError(f"could not run editor: {e}") from e
        if proc.returncode != 0:
            raise DocketError(f"editor exited with status {proc.returncode}; body not saved")
        try:
            new_body = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError as e:
            raise DocketError("edited body is not valid UTF-8; body not saved") from e
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if new_body == task["body"]:
        path.unlink(missing_ok=True)
        return task
    with db.write(conn):
        if get(conn, task["id"])["body"] != task["body"]:
            raise DocketError(
                f"{task['id']} body changed during the edit; not saved. Your edit is in {path}"
            )
        conn.execute("UPDATE tasks SET body = ? WHERE id = ?", (new_body, task["id"]))
        result = get(conn, task["id"])
    path.unlink(missing_ok=True)
    return result


def attach(conn: sqlite3.Connection, ref: str, paths: Sequence[str]) -> dict[str, Any]:
    sources = [Path(p) for p in paths]
    copied: list[Path] = []
    try:
        with db.write(conn):
            task_id = resolve_id(conn, ref)
            root = task_id.split(".", 1)[0]
            folder = db.store_path() / "attachments" / root
            seen: set[str] = set()
            for src in sources:
                if not src.is_file():
                    raise DocketError(f"not a file: {src}")
                if src.name in seen:
                    raise DocketError(f"duplicate attachment name: {src.name}")
                if (folder / src.name).exists():
                    raise DocketError(f"attachment already exists: attachments/{root}/{src.name}")
                seen.add(src.name)
            folder.mkdir(parents=True, exist_ok=True)
            for src in sources:
                dest = folder / src.name
                shutil.copy2(src, dest)
                copied.append(dest)
            conn.executemany(
                "INSERT OR IGNORE INTO task_links(task_id, link) VALUES (?, ?)",
                [(task_id, f"attachments/{root}/{s.name}") for s in sources],
            )
            return get(conn, task_id)
    except BaseException:
        for dest in copied:
            dest.unlink(missing_ok=True)
        raise


def read_body(file: str | None) -> str:
    """Body for `add`: --file, else stdin when it is not a TTY, else empty."""
    if file:
        try:
            raw = Path(file).read_bytes()
        except OSError as e:
            raise DocketError(f"cannot read {file}: {e.strerror or e}") from e
    elif sys.stdin is not None and not sys.stdin.isatty():
        raw = sys.stdin.buffer.read()
    else:
        return ""
    try:
        return raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError as e:
        raise DocketError("body is not valid UTF-8") from e


# --- reads ---


def ls(
    conn: sqlite3.Connection,
    scope: Scope,
    *,
    tag: str | None = None,
    status: str | None = None,
    archived: bool = False,
) -> list[dict[str, Any]]:
    if status is not None and status not in STATUSES:
        raise DocketError(f"invalid status '{status}'; use {', '.join(STATUSES)}")
    clause, params = _scope_sql(scope, with_unscoped=True)
    where = [clause]
    if not archived:
        where.append("t.archived IS NULL")
    if tag:
        where.append("EXISTS (SELECT 1 FROM task_tags g WHERE g.task_id = t.id AND g.tag = ?)")
        params.append(tag)
    if status:
        where.append("t.status = ?")
        params.append(status)
    return _select(
        conn,
        " AND ".join(where),
        params,
        order="t.archived IS NOT NULL, t.created, t.id",
        with_ready=True,
    )


def next_task(
    conn: sqlite3.Connection, scope: Scope, *, start: bool = False
) -> dict[str, Any] | None:
    """Oldest ready task in scope, with a parent summary. `start` also sets it to doing."""
    clause, params = _scope_sql(scope, with_unscoped=False)
    where = f"t.id IN (SELECT id FROM ready) AND {clause}"

    def pick() -> dict[str, Any] | None:
        rows = _select(conn, where, params)
        if not rows:
            return None
        task = rows[0]
        if start:
            conn.execute("UPDATE tasks SET status = 'doing' WHERE id = ?", (task["id"],))
            task = get(conn, task["id"])
        parent = None
        if task["parent"]:
            p = get(conn, task["parent"])
            parent = {"id": p["id"], "title": p["title"], "status": p["status"]}
        task["parent_summary"] = parent
        return task

    if not start:
        return pick()
    with db.write(conn):
        return pick()


def search(
    conn: sqlite3.Connection, query: str, scope: Scope, *, archived: bool = False
) -> list[dict[str, Any]]:
    terms = query.split()
    if not terms:
        raise DocketError("empty search query")
    match = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
    clause, params = _scope_sql(scope, with_unscoped=True)
    where = ["tasks_fts MATCH ?", clause]
    if not archived:
        where.append("t.archived IS NULL")
    try:
        return _select(
            conn,
            " AND ".join(where),
            [match, *params],
            frm="tasks_fts JOIN tasks t ON t.id = tasks_fts.id",
            order="bm25(tasks_fts, 0.0, 10.0, 1.0), t.created, t.id",
            with_ready=True,
        )
    except sqlite3.OperationalError as e:
        raise DocketError(f"search failed: {e}") from e


def index(conn: sqlite3.Connection, scope: Scope, limit: int = 20) -> dict[str, Any]:
    """Session summary: doing/review, then ready, capped at `limit`; the rest is a count."""
    clause, params = _scope_sql(scope, with_unscoped=True)
    open_tasks = _select(conn, f"t.archived IS NULL AND {clause}", params, with_ready=True)
    active = sorted(
        (t for t in open_tasks if t["status"] in ("doing", "review")),
        key=lambda t: (t["status"] != "doing", t["created"], t["id"]),
    )
    ready = [t for t in open_tasks if t["ready"]]
    listed = active + ready
    waiting = len(open_tasks) - len(listed)
    return {
        "tasks": listed[:limit],
        "waiting": waiting,
        "more": max(len(listed) - limit, 0),
    }
