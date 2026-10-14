"""Repo key resolution."""

from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from docket import db
from docket.db import DocketError


@dataclass(frozen=True)
class RepoInfo:
    key: str
    remote: str | None
    root: Path


@dataclass(frozen=True)
class Scope:
    """`key` None with `all_repos` False means "outside a repo"."""

    key: str | None
    all_repos: bool


def key_from_url(url: str) -> str:
    url = url.strip().rstrip("/")
    url = url.removesuffix(".git")
    parts = [p for p in re.split(r"[/:\\]", url) if p]
    return "/".join(parts[-2:])


def _git(cwd: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def detect(cwd: Path | None = None) -> RepoInfo | None:
    cwd = cwd or Path.cwd()
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    root = Path(top)
    remote = _git(cwd, "remote", "get-url", "origin")
    if remote:
        return RepoInfo(key=key_from_url(remote), remote=remote, root=root)
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    main = Path(common).parent if common else root
    return RepoInfo(key=main.name, remote=None, root=root)


def resolve_key(conn: sqlite3.Connection, arg: str) -> str:
    if "/" in arg:
        return arg
    known = db.known_repos(conn)
    matches = [k for k in known if k.rsplit("/", 1)[-1] == arg]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        hint = f"; known repos: {', '.join(known)}" if known else ""
        raise DocketError(f"no known repo matches '{arg}'{hint}")
    raise DocketError(f"repo '{arg}' is ambiguous: {', '.join(matches)}")


def scope(
    conn: sqlite3.Connection,
    repo_arg: str | None,
    all_repos: bool,
    cwd: Path | None = None,
) -> Scope:
    if all_repos:
        return Scope(key=None, all_repos=True)
    if repo_arg:
        return Scope(key=resolve_key(conn, repo_arg), all_repos=False)
    info = detect(cwd)
    return Scope(key=info.key if info else None, all_repos=False)
