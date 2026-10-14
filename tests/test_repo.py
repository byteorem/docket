import subprocess

import pytest

from docket import repo
from docket.db import DocketError


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://ghe.company.com/billing/api.git", "billing/api"),
        ("git@ghe.company.com:billing/api.git", "billing/api"),
        ("ssh://git@host.example.com:2222/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo/", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git/", "owner/repo"),
        ("C:\\src\\owner\\repo.git", "owner/repo"),
        ("https://host/group/sub/repo.git", "sub/repo"),
    ],
)
def test_key_from_url(url, key):
    assert repo.key_from_url(url) == key


def test_detect_https(git_repo):
    path = git_repo("a", origin="https://ghe.company.com/billing/api.git")
    info = repo.detect(path)
    assert info.key == "billing/api"
    assert info.remote == "https://ghe.company.com/billing/api.git"
    assert info.root.resolve() == path.resolve()


def test_detect_scp_style(git_repo):
    assert repo.detect(git_repo("a", origin="git@host:billing/api.git")).key == "billing/api"


def test_detect_subfolder(git_repo):
    path = git_repo("a", origin="https://h/o/r.git")
    sub = path / "x" / "y"
    sub.mkdir(parents=True)
    info = repo.detect(sub)
    assert info.key == "o/r"
    assert info.root.resolve() == path.resolve()


def test_detect_linked_worktree(git_repo, tmp_path):
    path = git_repo("main-checkout", origin="https://h/o/r.git")
    wt = tmp_path / "wt"
    repo_git = ["git", "-C", str(path), "worktree", "add", "-q", str(wt), "-b", "feature"]
    subprocess.run(repo_git, check=True, capture_output=True)
    assert repo.detect(wt).key == "o/r"


def test_detect_no_origin_uses_main_worktree_folder(git_repo, tmp_path):
    path = git_repo("my-project")
    info = repo.detect(path)
    assert info.key == "my-project"
    assert info.remote is None
    wt = tmp_path / "other-name"
    subprocess.run(
        ["git", "-C", str(path), "worktree", "add", "-q", str(wt), "-b", "f"],
        check=True,
        capture_output=True,
    )
    assert repo.detect(wt).key == "my-project"


def test_detect_outside_repo(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    assert repo.detect(outside) is None


def test_detect_git_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert repo.detect(tmp_path) is None


def _seed(conn, *keys):
    from docket import db

    for i, key in enumerate(keys):
        tid = f"t{i}"
        conn.execute(
            "INSERT INTO tasks(id, title, status, created) VALUES (?, 't', 'todo', ?)",
            (tid, db.now()),
        )
        conn.execute("INSERT INTO task_repos VALUES (?, ?)", (tid, key))


@pytest.fixture
def conn(home):
    from docket import db

    c = db.connect()
    yield c
    c.close()


def test_resolve_key_full_key_passthrough(conn):
    assert repo.resolve_key(conn, "new/repo") == "new/repo"


def test_resolve_key_bare_unique(conn):
    _seed(conn, "billing/api", "billing/web")
    assert repo.resolve_key(conn, "api") == "billing/api"


def test_resolve_key_bare_ambiguous(conn):
    _seed(conn, "billing/api", "other/api")
    with pytest.raises(DocketError, match=r"billing/api.*other/api"):
        repo.resolve_key(conn, "api")


def test_resolve_key_bare_unknown(conn):
    _seed(conn, "billing/api")
    with pytest.raises(DocketError, match="nope"):
        repo.resolve_key(conn, "nope")


def test_scope(conn, git_repo, tmp_path):
    path = git_repo("a", origin="https://h/o/r.git")
    assert repo.scope(conn, None, False, path) == repo.Scope("o/r", False)
    assert repo.scope(conn, "x/y", False, path) == repo.Scope("x/y", False)
    assert repo.scope(conn, None, True, path) == repo.Scope(None, True)
    outside = tmp_path / "plain"
    outside.mkdir()
    assert repo.scope(conn, None, False, outside) == repo.Scope(None, False)
