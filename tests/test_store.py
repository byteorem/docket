import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import git

from docket.store import atomic_write


def test_where_in_main_worktree(git_repo, ok):
    repo = git_repo("app")
    assert ok("where", cwd=repo) == {
        "store": str(repo / ".git" / "docket"),
        "git_dir": str(repo / ".git"),
        "worktree": str(repo),
    }


def test_where_from_subfolder(git_repo, ok):
    repo = git_repo("app")
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    data = ok("where", cwd=sub)
    assert data["store"] == str(repo / ".git" / "docket")
    assert data["worktree"] == str(repo)


def test_linked_worktree_shares_the_main_store(git_repo, ok, mk, tmp_path):
    repo = git_repo("app")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(linked), "-b", "feature")

    data = ok("where", cwd=linked)
    assert data["store"] == str(repo / ".git" / "docket")
    assert data["git_dir"] == str(repo / ".git" / "worktrees" / "linked")
    assert data["worktree"] == str(linked)

    task_id = mk("From the worktree", cwd=linked)
    assert [t["id"] for t in ok("ls", cwd=repo)] == [task_id]


def test_outside_a_repo_reads_are_empty_and_writes_fail(run, ok, fail, tmp_path):
    assert ok("where", cwd=tmp_path) == {"store": None, "git_dir": None, "worktree": None}
    assert ok("ls") == []
    assert run("index").stdout == ""
    error = fail("create", "Nowhere")
    assert "git repo" in error and "DOCKET_DIR" in error


def test_docket_dir_overrides_discovery(git_repo, ok, monkeypatch, tmp_path):
    repo = git_repo("app")
    monkeypatch.setenv("DOCKET_DIR", "elsewhere")
    data = ok("where", cwd=repo)
    assert data == {"store": str(repo / "elsewhere"), "git_dir": None, "worktree": None}


def test_reads_do_not_create_the_store(git_repo, ok, mk):
    repo = git_repo("app")
    ok("ls", cwd=repo)
    ok("check", cwd=repo)
    assert not (repo / ".git" / "docket").exists()
    mk("First", cwd=repo)
    assert (repo / ".git" / "docket" / "tasks").is_dir()
    assert not (repo / ".git" / "docket" / ".lock").exists()


def test_git_never_sees_the_store(git_repo, mk):
    repo = git_repo("app")
    mk("Hidden", cwd=repo)
    assert git(repo, "status", "--porcelain") == ""


# --- config ---


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_config_defaults(store, ok, mk):
    task = ok("show", mk("Default"))
    assert task["status"] == "open"
    assert ok("close", task["id"])["task"]["status"] == "closed"


def test_store_config_overrides_user_config(store, ok, mk, tmp_path):
    write(tmp_path / "docket-home" / "config.yaml", "default_status: todo\nclosed_statuses: [x]\n")
    write(store / "config.yaml", "closed_statuses: [done, wontfix]\n")
    task_id = mk("Configured")
    assert ok("show", task_id)["status"] == "todo"
    assert ok("close", task_id, "--status", "wontfix")["task"]["state"] == "closed"
    assert ok("reopen", task_id)["task"]["status"] == "todo"
    assert ok("close", task_id)["task"]["status"] == "done"


def test_other_statuses_count_as_open(store, ok, mk):
    write(store / "config.yaml", "closed_statuses: [done]\n")
    task_id = mk("Task")
    task = ok("set", task_id, "status", "closed")["task"]
    assert task["state"] == "ready"
    assert "closed_at" not in task


@pytest.mark.parametrize(
    "text, message",
    [
        ("closed_statuses: []\n", "closed_statuses"),
        ("closed_statuses: done\n", "closed_statuses"),
        ("default_status: done\nclosed_statuses: [done]\n", "also in closed_statuses"),
        ("defualt_status: todo\n", "unknown key 'defualt_status'"),
        ("[a, b]\n", "expected a mapping"),
        ("default_status: [\n", "invalid YAML"),
    ],
)
def test_invalid_config_is_a_command_error(store, fail, text, message):
    write(store / "config.yaml", text)
    assert message in fail("ls")


# --- lock and writes ---


def test_stale_lock_is_removed(store, mk):
    lock = store / ".lock"
    write(lock, "123")
    old = time.time() - 120
    os.utime(lock, (old, old))
    mk("After a crash")
    assert not lock.exists()


def test_fresh_lock_times_out(store, fail, monkeypatch):
    monkeypatch.setattr("docket.store.LOCK_TIMEOUT", 0.2)
    write(store / ".lock", "123")
    assert "locked" in fail("create", "Blocked")
    assert (store / ".lock").exists()
    assert list((store / "tasks").glob("*.md")) == []


def test_replace_retries_on_permission_error(tmp_path, monkeypatch):
    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("in use")
        real_replace(src, dst)

    monkeypatch.setattr("docket.store.time.sleep", lambda _: None)
    monkeypatch.setattr("docket.store.os.replace", flaky)
    target = tmp_path / "t.md"
    atomic_write(target, b"data")
    assert target.read_bytes() == b"data"
    assert len(calls) == 3
    assert list(tmp_path.glob("*.tmp")) == []


def test_replace_gives_up_after_five_retries(tmp_path, monkeypatch):
    def always(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr("docket.store.time.sleep", lambda _: None)
    monkeypatch.setattr("docket.store.os.replace", always)
    with pytest.raises(PermissionError):
        atomic_write(tmp_path / "t.md", b"data")
    assert list(tmp_path.iterdir()) == []


def test_two_processes_creating_at_once(store, mk, ok):
    parent = mk("Parent")
    script = (
        "import sys\n"
        "from docket.cli import run\n"
        "for i in range(15):\n"
        "    assert run(['create', f'child {i}', '--parent', sys.argv[1]]) == 0\n"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", script, parent], stdin=subprocess.DEVNULL)
        for _ in range(2)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    children = [t for t in ok("ls") if t["parent"] == parent]
    assert sorted(int(t["id"].split(".")[1]) for t in children) == list(range(1, 31))
    assert ok("check") == {"problems": []}
