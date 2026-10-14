import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from docket.cli import app


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "docket-home"
    monkeypatch.setenv("DOCKET_HOME", str(path))
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
def git_repo(tmp_path: Path) -> Callable[..., Path]:
    """Factory: git_repo("name", origin="https://...") makes a repo with one commit."""

    def make(name: str, origin: str | None = None) -> Path:
        path = tmp_path / "repos" / name
        path.mkdir(parents=True)
        git(path, "init", "-q", "-b", "main")
        git(path, "config", "user.name", "Test")
        git(path, "config", "user.email", "test@example.com")
        git(path, "config", "commit.gpgsign", "false")
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
        if origin:
            git(path, "remote", "add", "origin", origin)
        return path

    return make


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Result]:
    """Invoke the CLI in-process: run("where", "--json", cwd=path)."""
    runner = CliRunner()

    def invoke(*args: str, cwd: Path | None = None) -> Result:
        if cwd is not None:
            monkeypatch.chdir(cwd)
        return runner.invoke(app, list(args))

    return invoke
