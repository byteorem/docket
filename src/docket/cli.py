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
            typer.echo(
                f"warning: no origin remote; using folder name '{key}' as the repo key", err=True
            )
        else:
            source = remote

    store = str(db.store_path())
    data = {"store": store, "repo": key, "remote": remote}
    emit(
        data,
        lambda: f"store  {store}\nrepo   {key or 'not in a repo'}\nremote {source}",
        as_json,
    )


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
