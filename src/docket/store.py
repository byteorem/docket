"""Store discovery, config, the write lock, and atomic file writes."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_TIMEOUT = 10.0
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
    editor: str | None = None


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
    if not _is_line(default):
        raise DocketError("config: default_status must be a one-line string")
    if not isinstance(closed, list) or not closed or not all(_is_line(s) for s in closed):
        raise DocketError("config: closed_statuses must be a non-empty list of one-line strings")
    if default in closed:
        raise DocketError(f"config: default_status '{default}' is also in closed_statuses")
    editor = merged.get("editor")
    if editor is not None and not _is_line(editor):
        raise DocketError("config: editor must be a one-line command")
    return Config(default_status=default, closed_statuses=tuple(closed), editor=editor)


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
        if key not in ("default_status", "closed_statuses", "editor"):
            raise DocketError(f"{path}: unknown key '{key}'")
    return data


def _is_line(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.splitlines() == [value]


# --- writing ---


@contextmanager
def lock(store: Path) -> Iterator[None]:
    """Hold the store's write lock, creating the store folders on first write.

    The lock is an OS lock on .lock, so the OS releases it when a writer dies and
    there is never a stale lock to clear. The file itself stays.
    """
    (store / "tasks").mkdir(parents=True, exist_ok=True)
    fd = os.open(store / ".lock", os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise DocketError("store is locked by another docket write; try again")
            time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    # Windows may release a lock some time after its handle closes, so unlock first.
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


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
