"""Typer app: argument parsing and text/JSON output. Command logic lives elsewhere."""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    pretty_exceptions_enable=False,
    no_args_is_help=True,
    add_completion=False,
    help="Personal local task tracker.",
)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print one JSON value.")]
RepoOpt = Annotated[
    str | None, typer.Option("--repo", metavar="KEY", help="Repo key (owner/repo or bare repo).")
]
AllReposOpt = Annotated[bool, typer.Option("--all-repos", help="Do not filter by repo.")]


@app.callback()
def _root() -> None:
    """Personal local task tracker."""


def guarded[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Apply under `@app.command()`: DocketError becomes one stderr line and exit 1."""
    from docket.db import DocketError

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except DocketError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from None

    return wrapper


def emit(data: Any, text: str | Callable[[], str], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        typer.echo(text() if callable(text) else text)


@app.command()
@guarded
def where(repo: RepoOpt = None, as_json: JsonOpt = False) -> None:
    """Print the store path, current repo key, and the remote it came from."""
    from docket import db
    from docket import repo as repo_mod

    key: str | None
    remote: str | None = None
    if repo:
        conn = db.connect()
        try:
            key = repo_mod.resolve_key(conn, repo)
        finally:
            conn.close()
        source = "(from --repo)"
    else:
        info = repo_mod.detect()
        key = info.key if info else None
        remote = info.remote if info else None
        if info is None:
            source = "-"
        elif remote is None:
            source = "no origin"
            warn_no_origin(info)
        else:
            source = remote

    store = str(db.store_path())
    data = {"store": store, "repo": key, "remote": remote}
    emit(
        data,
        lambda: f"store  {store}\nrepo   {key or 'not in a repo'}\nremote {source}",
        as_json,
    )


# --- task commands ---

TagOpt = Annotated[list[str] | None, typer.Option("--tag", help="Tag (repeatable).")]
DepOpt = Annotated[list[str] | None, typer.Option("--dep", metavar="ID", help="Repeatable.")]
AddRepoOpt = Annotated[
    list[str] | None, typer.Option("--repo", metavar="KEY", help="Repo key (repeatable).")
]


def warn_no_origin(info: Any) -> None:
    if info is not None and info.remote is None:
        typer.echo(
            f"warning: no origin remote; using folder name '{info.key}' as the repo key", err=True
        )


def _scope(conn: Any, repo: str | None, all_repos: bool) -> Any:
    from docket import repo as repo_mod

    if all_repos or repo:
        return repo_mod.scope(conn, repo, all_repos)
    info = repo_mod.detect()
    warn_no_origin(info)
    return repo_mod.Scope(key=info.key if info else None, all_repos=False)


def _open() -> Any:
    from contextlib import closing

    from docket import db

    return closing(db.connect())


def _repos_text(task: dict[str, Any]) -> str:
    return ", ".join(task["repos"]) or "(none)"


def _written(task: dict[str, Any], as_json: bool) -> None:
    emit(task, f"{task['id']}  repos: {_repos_text(task)}", as_json)


def _task_text(task: dict[str, Any], parent_summary: dict[str, Any] | None = None) -> str:
    lines = []
    for key, value in task.items():
        if key in ("body", "ready", "parent_summary") or value in (None, "", []):
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        lines.append(f"{key}: {shown}")
        if key == "parent" and parent_summary:
            lines[-1] += f" ({parent_summary['title']}; {parent_summary['status']})"
    if task["body"]:
        lines += ["", task["body"]]
    return "\n".join(lines)


def _entry_lines(tasks: list[dict[str, Any]]) -> list[str]:
    width = max((len(t["id"]) for t in tasks), default=0)
    lines = []
    for t in tasks:
        state = t["status"] or t["outcome"]
        mark = "ready" if t.get("ready") else "-"
        lines.append(f"{t['id']:<{width}}  {state:<8}  {mark:<5}  {t['title']}")
    return lines


def _emit_entries(found: list[dict[str, Any]], as_json: bool) -> None:
    if found or as_json:
        emit(found, lambda: "\n".join(_entry_lines(found)), as_json)


@app.command()
@guarded
def add(
    title: Annotated[str, typer.Argument(help="Task title.")],
    parent: Annotated[str | None, typer.Option("--parent", metavar="ID")] = None,
    dep: DepOpt = None,
    tag: TagOpt = None,
    repo: AddRepoOpt = None,
    no_repo: Annotated[bool, typer.Option("--no-repo", help="Put the task in no repo.")] = False,
    from_: Annotated[str | None, typer.Option("--from", metavar="ID")] = None,
    file: Annotated[str | None, typer.Option("--file", help="Read the body from PATH.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Create a task. Body from --file or stdin."""
    from docket import repo as repo_mod
    from docket import tasks

    if repo and no_repo:
        raise typer.BadParameter("--repo and --no-repo are mutually exclusive")
    with _open() as conn:
        if no_repo:
            repos: list[str] = []
        elif repo:
            repos = [repo_mod.resolve_key(conn, r) for r in repo]
        else:
            info = repo_mod.detect()
            warn_no_origin(info)
            repos = [info.key] if info else []
        body = tasks.read_body(file)
        task = tasks.add(
            conn,
            title,
            body=body,
            parent=parent,
            deps=dep or [],
            tags=tag or [],
            repos=repos,
            discovered_from=from_,
        )
    _written(task, as_json)


@app.command()
@guarded
def show(id: Annotated[str, typer.Argument()], as_json: JsonOpt = False) -> None:
    """Print a task with its fields and body."""
    from docket import tasks

    with _open() as conn:
        task = tasks.get(conn, tasks.resolve_id(conn, id))
    emit(task, lambda: _task_text(task), as_json)


@app.command()
@guarded
def ls(
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    archived: Annotated[bool, typer.Option("--archived", help="Include archived tasks.")] = False,
    repo: RepoOpt = None,
    all_repos: AllReposOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """List tasks in scope with status and ready state."""
    from docket import tasks

    with _open() as conn:
        found = tasks.ls(
            conn, _scope(conn, repo, all_repos), tag=tag, status=status, archived=archived
        )
    _emit_entries(found, as_json)


@app.command(name="next")
@guarded
def next_(
    start: Annotated[bool, typer.Option("--start", help="Also set the task to doing.")] = False,
    repo: RepoOpt = None,
    all_repos: AllReposOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Print the oldest ready task in scope."""
    from docket import tasks

    with _open() as conn:
        task = tasks.next_task(conn, _scope(conn, repo, all_repos), start=start)
    if task is None:
        if as_json:
            typer.echo("null")
        return
    emit(task, lambda: _task_text(task, task["parent_summary"]), as_json)


@app.command(name="set")
@guarded
def set_(
    id: Annotated[str, typer.Argument()],
    field: Annotated[str, typer.Argument()],
    value: Annotated[str | None, typer.Argument()] = None,
    add: Annotated[list[str] | None, typer.Option("--add", metavar="V")] = None,
    remove: Annotated[list[str] | None, typer.Option("--remove", metavar="V")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Set a field. Lists (tags, repos, links): a value replaces; --add/--remove edit."""
    from docket import tasks

    if field in tasks.LIST_FIELDS:
        if value is None and not add and not remove:
            raise typer.BadParameter(f"{field} needs a value, --add, or --remove")
    elif field in tasks.SCALAR_FIELDS:
        if value is None:
            raise typer.BadParameter(f"{field} needs a value")
        if add or remove:
            raise typer.BadParameter(
                f"--add and --remove only apply to {', '.join(tasks.LIST_FIELDS)}"
            )
    else:
        fields = ", ".join([*tasks.SCALAR_FIELDS, *tasks.LIST_FIELDS])
        raise typer.BadParameter(f"unknown field '{field}'; use {fields}")
    with _open() as conn:
        task = tasks.set_field(conn, id, field, value, add or [], remove or [])
    _written(task, as_json)


@app.command()
@guarded
def edit(id: Annotated[str, typer.Argument()], as_json: JsonOpt = False) -> None:
    """Edit the body in $VISUAL or $EDITOR."""
    from docket import tasks

    with _open() as conn:
        task = tasks.edit(conn, id)
    _written(task, as_json)


@app.command()
@guarded
def note(
    id: Annotated[str, typer.Argument()],
    text: Annotated[str, typer.Argument()],
    as_json: JsonOpt = False,
) -> None:
    """Append a timestamped line to the body."""
    from docket import tasks

    with _open() as conn:
        task = tasks.note(conn, id, text)
    _written(task, as_json)


@app.command()
@guarded
def dep(
    id: Annotated[str, typer.Argument()],
    dep_id: Annotated[str, typer.Argument()],
    as_json: JsonOpt = False,
) -> None:
    """Add a dependency: ID waits on DEP_ID."""
    from docket import tasks

    with _open() as conn:
        task = tasks.dep(conn, id, dep_id)
    _written(task, as_json)


@app.command()
@guarded
def undep(
    id: Annotated[str, typer.Argument()],
    dep_id: Annotated[str, typer.Argument()],
    as_json: JsonOpt = False,
) -> None:
    """Remove a dependency."""
    from docket import tasks

    with _open() as conn:
        task = tasks.undep(conn, id, dep_id)
    _written(task, as_json)


@app.command()
@guarded
def attach(
    id: Annotated[str, typer.Argument()],
    paths: Annotated[list[str], typer.Argument(metavar="PATH...")],
    as_json: JsonOpt = False,
) -> None:
    """Copy files into the task's attachment folder and link them."""
    from docket import tasks

    with _open() as conn:
        task = tasks.attach(conn, id, paths)
    _written(task, as_json)


@app.command()
@guarded
def complete(id: Annotated[str, typer.Argument()], as_json: JsonOpt = False) -> None:
    """Archive with outcome done."""
    from docket import tasks

    with _open() as conn:
        task = tasks.complete(conn, id)
    _written(task, as_json)


@app.command()
@guarded
def cancel(id: Annotated[str, typer.Argument()], as_json: JsonOpt = False) -> None:
    """Archive with outcome canceled."""
    from docket import tasks

    with _open() as conn:
        task = tasks.cancel(conn, id)
    _written(task, as_json)


@app.command()
@guarded
def reopen(id: Annotated[str, typer.Argument()], as_json: JsonOpt = False) -> None:
    """Return an archived task to open (status doing)."""
    from docket import tasks

    with _open() as conn:
        task = tasks.reopen(conn, id)
    _written(task, as_json)


@app.command()
@guarded
def search(
    query: Annotated[str, typer.Argument()],
    archived: Annotated[bool, typer.Option("--archived", help="Include archived tasks.")] = False,
    repo: RepoOpt = None,
    all_repos: AllReposOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Full-text search in scope, best match first."""
    from docket import tasks

    with _open() as conn:
        found = tasks.search(conn, query, _scope(conn, repo, all_repos), archived=archived)
    _emit_entries(found, as_json)


@app.command()
def index(
    limit: Annotated[int, typer.Option("--limit", min=0, help="Max tasks listed.")] = 20,
    repo: RepoOpt = None,
    all_repos: AllReposOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Print the session summary. Always exits 0."""
    from docket import tasks

    try:
        with _open() as conn:
            scope = _scope(conn, repo, all_repos)
            data = tasks.index(conn, scope, limit)
        if scope.all_repos:
            header = "all repos"
        elif scope.key:
            header = f"repo {scope.key}"
        else:
            header = "no repo"
        data = {"repo": scope.key, "all_repos": scope.all_repos, **data}

        def text() -> str:
            listed = data["tasks"]
            width = max((len(t["id"]) for t in listed), default=0)
            lines = [header]
            for t in listed:
                state = "ready" if t["ready"] else t["status"]
                lines.append(f"  {state:<7} {t['id']:<{width}}  {t['title']}")
            if data["more"]:
                lines.append(f"  +{data['more']} more")
            if data["waiting"]:
                lines.append(f"  +{data['waiting']} waiting")
            return "\n".join(lines)

        emit(data, text, as_json)
    except Exception as e:
        typer.echo(f"docket index: {e}", err=True)


# --- maintenance and install commands ---


@app.command()
@guarded
def doctor(as_json: JsonOpt = False) -> None:
    """Check store integrity; exit 1 when anything is found."""
    from docket import checks, db

    conn = db.connect()
    try:
        data = checks.doctor(conn)
    finally:
        conn.close()

    def text() -> str:
        lines = [f"integrity: {m}" for m in data["integrity"]]
        lines += [
            f"foreign key: {r['table']} rowid {r['rowid']} -> missing {r['parent']}"
            for r in data["foreign_keys"]
        ]
        lines += [f"orphan attachment folder: {d}" for d in data["orphan_attachment_dirs"]]
        lines += [
            f"missing attachment: {m['task']} {m['link']}" for m in data["missing_attachments"]
        ]
        return "\n".join(lines) or "ok"

    emit(data, text, as_json)
    if not data["ok"]:
        raise typer.Exit(1)


@app.command()
@guarded
def export(
    fmt: Annotated[str, typer.Option("--format", help="jsonl or md.")] = "jsonl",
    out: Annotated[Path | None, typer.Option("--out", help="Output folder.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Dump all tasks: jsonl to stdout or DIR/docket.jsonl; md files need --out."""
    from docket import checks

    if fmt not in ("jsonl", "md"):
        raise typer.BadParameter("use jsonl or md", param_hint="--format")
    if fmt == "md" and out is None:
        raise typer.BadParameter("md export needs --out", param_hint="--out")
    with _open() as conn:
        data = checks.export(conn, fmt, out)
    if out is None:
        if as_json:
            typer.echo(json.dumps(data["tasks"], ensure_ascii=False))
        else:
            for t in data["tasks"]:
                typer.echo(json.dumps(t, ensure_ascii=False))
        return
    summary = {k: data[k] for k in ("format", "path", "count")}
    emit(summary, f"exported {data['count']} tasks to {data['path']}", as_json)


install_app = typer.Typer(no_args_is_help=True, help="Install agent integration.")
app.add_typer(install_app, name="install")


@install_app.command("skill")
@guarded
def install_skill(
    dir: Annotated[
        Path | None,
        typer.Option("--dir", help="Skills folder to install into (default ~/.claude/skills)."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Write the bundled skill to <dir>/docket/SKILL.md."""
    from docket import install

    data = install.install_skill(dir)
    emit(data, f"wrote {data['path']}", as_json)


@install_app.command("hook")
@guarded
def install_hook(as_json: JsonOpt = False) -> None:
    """Add the SessionStart hook and docket permission rules to Claude Code settings."""
    from docket import install

    data = install.install_hook()

    def text() -> str:
        if not data["added"]:
            return f"already installed; nothing changed\n{data['path']}"
        lines = [f"added {a}" for a in data["added"]]
        lines.append(f"settings {data['path']}")
        if data["backup"]:
            lines.append(f"backup   {data['backup']}")
        return "\n".join(lines)

    emit(data, text, as_json)
    warning = install.path_warning()
    if warning:
        typer.echo(warning, err=True)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    app()
