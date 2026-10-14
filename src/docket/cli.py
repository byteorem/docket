"""Typer app: argument parsing and text/JSON output. Command logic lives elsewhere."""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
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


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    app()
