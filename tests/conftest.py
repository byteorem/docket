import io
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from docket import cli


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the real ~/.docket and any DOCKET_DIR in the shell."""
    monkeypatch.setenv("DOCKET_HOME", str(tmp_path / "docket-home"))
    monkeypatch.delenv("DOCKET_DIR", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A store picked by DOCKET_DIR, for tests that don't care about discovery."""
    path = tmp_path / "store"
    monkeypatch.setenv("DOCKET_DIR", str(path))
    return path


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Callable[[str], Path]:
    """Factory: git_repo("app") makes a repo with one commit."""

    def make(name: str) -> Path:
        path = tmp_path / "repos" / name
        path.mkdir(parents=True)
        git(path, "init", "-q", "-b", "main")
        git(path, "config", "user.name", "Test")
        git(path, "config", "user.email", "test@example.com")
        git(path, "config", "commit.gpgsign", "false")
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
        return path

    return make


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str

    def json(self) -> Any:
        return json.loads(self.stdout)


Run = Callable[..., Result]


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Run:
    """Invoke the CLI in-process: run("ls", "--json", cwd=path, stdin="body")."""

    def invoke(*args: str, cwd: Path | None = None, stdin: str = "") -> Result:
        if cwd is not None:
            monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        capsys.readouterr()
        code = cli.run(list(args))
        out, err = capsys.readouterr()
        return Result(code, out, err)

    return invoke


@pytest.fixture
def ok(run: Run) -> Callable[..., Any]:
    """Run with --json, assert exit 0, and return the parsed output."""

    def invoke(*args: str, **kwargs: Any) -> Any:
        result = run(*args, "--json", **kwargs)
        assert result.code == 0, result.stderr
        return result.json() if result.stdout else None

    return invoke


@pytest.fixture
def fail(run: Run) -> Callable[..., str]:
    """Run with --json, assert exit 1 with empty stdout and one stderr line; return stderr."""

    def invoke(*args: str, **kwargs: Any) -> str:
        result = run(*args, "--json", **kwargs)
        assert result.code == 1, (result.code, result.stdout, result.stderr)
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1, result.stderr
        return result.stderr

    return invoke


@pytest.fixture
def mk(ok: Callable[..., Any]) -> Callable[..., str]:
    """Create a task and return its ID: mk("title", "--parent", pid)."""

    def make(title: str, *args: str, **kwargs: Any) -> str:
        return ok("create", title, *args, **kwargs)["task"]["id"]

    return make
