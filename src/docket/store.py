"""Store discovery, config, the write lock, and atomic file writes."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_TIMEOUT = 10.0
LOCK_STALE = 60.0
REPLACE_RETRIES = 5

NO_STORE = "not in a git repo; run docket inside one, or set DOCKET_DIR to a folder"


class DocketError(Exception):
    """A command error: one line on stderr, exit 1."""


@dataclass(frozen=True)
class Location:
    store: Path | None
    git_dir: Path | None = None
    worktree: Path | None = None

    def require(self) -> Path:
        """The store path, for commands that write."""
        if self.store is None:
            raise DocketError(NO_STORE)
        return self.store


def discover(cwd: Path | None = None) -> Location:
    env = os.environ.get("DOCKET_DIR")
    if env:
        return Location(store=Path(os.path.abspath(env)))
    start = cwd or Path.cwd()
    for folder in (start, *start.parents):
        dot_git = folder / ".git"
        if dot_git.is_dir():
            git_dir = dot_git
        elif dot_git.is_file():
            git_dir = _follow_gitfile(dot_git)
        else:
            continue
        common = git_dir
        commondir = git_dir / "commondir"
        if commondir.is_file():
            common = _join(git_dir, commondir.read_text(encoding="utf-8").strip())
        return Location(store=common / "docket", git_dir=git_dir, worktree=folder)
    return Location(store=None)


def _follow_gitfile(path: Path) -> Path:
    first = path.read_text(encoding="utf-8-sig").partition("\n")[0]
    if not first.startswith("gitdir:"):
        raise DocketError(f"{path}: expected 'gitdir: <path>'")
    return _join(path.parent, first.removeprefix("gitdir:").strip())


def _join(base: Path, path: str) -> Path:
    """Join lexically, so mapped drives and junctions stay as git wrote them."""
    return Path(os.path.normpath(base / path))


# --- config ---


@dataclass(frozen=True)
class Config:
    default_status: str = "open"
    closed_statuses: tuple[str, ...] = ("closed",)


def user_home() -> Path:
    env = os.environ.get("DOCKET_HOME")
    return Path(env) if env else Path.home() / ".docket"


def load_config(store: Path | None) -> Config:
    """Defaults, overridden by the user's config.yaml, overridden by the store's."""
    merged: dict[str, Any] = {}
    paths = [user_home() / "config.yaml"]
    if store is not None:
        paths.append(store / "config.yaml")
    for path in paths:
        merged.update(_read_config(path))

    default = merged.get("default_status", Config.default_status)
    closed = merged.get("closed_statuses", list(Config.closed_statuses))
    if not _is_status(default):
        raise DocketError("config: default_status must be a one-line string")
    if not isinstance(closed, list) or not closed or not all(_is_status(s) for s in closed):
        raise DocketError("config: closed_statuses must be a non-empty list of one-line strings")
    if default in closed:
        raise DocketError(f"config: default_status '{default}' is also in closed_statuses")
    return Config(default_status=default, closed_statuses=tuple(closed))


def _read_config(path: Path) -> dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as e:
        raise DocketError(f"{path}: invalid YAML ({' '.join(str(e).split())})") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise DocketError(f"{path}: expected a mapping")
    for key in data:
        if key not in ("default_status", "closed_statuses"):
            raise DocketError(f"{path}: unknown key '{key}'")
    return data


def _is_status(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.splitlines() == [value]


# --- writing ---


@contextmanager
def lock(store: Path) -> Iterator[None]:
    """Hold the store's write lock, creating the store folders on first write."""
    (store / "tasks").mkdir(parents=True, exist_ok=True)
    path = store / ".lock"
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            # Windows raises PermissionError while another process is deleting the lock.
            if _is_stale(path):
                with suppress(OSError):
                    path.unlink()
                continue
            if time.monotonic() >= deadline:
                raise DocketError(
                    f"store is locked by another write; if no docket command is running, "
                    f"delete {path}"
                ) from None
            time.sleep(0.02)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def _is_stale(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > LOCK_STALE
    except OSError:
        return False


def atomic_write(path: Path, data: bytes) -> None:
    """Write through a temp file in the same folder, then replace PATH."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        for attempt in range(REPLACE_RETRIES + 1):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows refuses while a reader or scanner holds PATH open.
                if attempt == REPLACE_RETRIES:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        Path(tmp).unlink(missing_ok=True)
