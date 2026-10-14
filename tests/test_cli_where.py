import json
import os
import subprocess
import sys

from docket import db


def test_where_in_repo_with_origin(home, git_repo, run):
    path = git_repo("a", origin="https://ghe.company.com/billing/api.git")
    result = run("where", cwd=path)
    assert result.exit_code == 0
    assert str(home) in result.stdout
    assert "billing/api" in result.stdout
    assert "https://ghe.company.com/billing/api.git" in result.stdout


def test_where_json(home, git_repo, run):
    path = git_repo("a", origin="git@host:billing/api.git")
    result = run("where", "--json", cwd=path)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "store": str(home),
        "repo": "billing/api",
        "remote": "git@host:billing/api.git",
    }


def test_where_no_origin_warns(home, git_repo, run):
    path = git_repo("folder-name")
    result = run("where", "--json", cwd=path)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"store": str(home), "repo": "folder-name", "remote": None}
    assert "no origin" in result.stderr
    text = run("where", cwd=path)
    assert "no origin" in text.stdout


def test_where_outside_repo(home, tmp_path, run):
    outside = tmp_path / "plain"
    outside.mkdir()
    result = run("where", cwd=outside)
    assert result.exit_code == 0
    assert "not in a repo" in result.stdout
    data = json.loads(run("where", "--json", cwd=outside).stdout)
    assert data["repo"] is None
    assert data["remote"] is None


def test_where_repo_option(home, tmp_path, run):
    outside = tmp_path / "plain"
    outside.mkdir()
    data = json.loads(run("where", "--repo", "o/r", "--json", cwd=outside).stdout)
    assert data["repo"] == "o/r"


def test_where_repo_option_error_is_one_line_on_stderr(home, tmp_path, run):
    outside = tmp_path / "plain"
    outside.mkdir()
    result = run("where", "--repo", "unknown", "--json", cwd=outside)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "unknown" in result.stderr


def test_where_bare_repo_resolves_known(home, tmp_path, run):
    conn = db.connect()
    conn.execute("INSERT INTO tasks(id, title, status, created) VALUES ('a1', 't', 'todo', 'x')")
    conn.execute("INSERT INTO task_repos VALUES ('a1', 'billing/api')")
    conn.close()
    assert json.loads(run("where", "--repo", "api", "--json", cwd=tmp_path).stdout)["repo"] == (
        "billing/api"
    )


def test_usage_error_exits_2(run):
    assert run("where", "--bogus").exit_code == 2


def test_no_args_shows_help(run):
    result = run()
    assert "where" in result.stdout


def test_where_real_subprocess(home, git_repo):
    path = git_repo("a", origin="https://h/o/r.git")
    env = {**os.environ, "DOCKET_HOME": str(home)}
    proc = subprocess.run(
        [sys.executable, "-m", "docket", "where", "--json"],
        cwd=path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["repo"] == "o/r"
