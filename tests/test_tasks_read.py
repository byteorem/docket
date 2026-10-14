import json

import pytest
from helpers import js, mk, ok, set_created


@pytest.fixture
def plain(home, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    return d


@pytest.fixture
def world(home, git_repo, plain, run):
    """A repo billing/api, plus tasks: one in it, one in other/repo, one in both, one unscoped."""
    repo = git_repo("a", origin="https://h/billing/api.git")
    ids = {
        "api": mk(run, repo, "api task", "--repo", "billing/api"),
        "other": mk(run, repo, "other task", "--repo", "other/repo"),
        "both": mk(run, repo, "both task", "--repo", "billing/api", "--repo", "other/repo"),
        "free": mk(run, repo, "free task", "--no-repo"),
    }
    for n, task_id in enumerate(ids.values()):
        set_created(task_id, f"2026-01-0{n + 1}T00:00:00Z")
    return repo, plain, ids


def titles(rows):
    return {r["title"] for r in rows}


# --- scope ---


def test_ls_scope(world, run):
    repo, plain, _ = world
    assert titles(js(run, repo, "ls")) == {"api task", "both task", "free task"}
    assert titles(js(run, plain, "ls")) == {"free task"}
    assert titles(js(run, plain, "ls", "--repo", "other/repo")) == {
        "other task",
        "both task",
        "free task",
    }
    assert titles(js(run, plain, "ls", "--repo", "repo")) == titles(
        js(run, plain, "ls", "--repo", "other/repo")
    )
    assert len(js(run, repo, "ls", "--all-repos")) == 4


def test_next_scope(world, run):
    repo, plain, ids = world
    assert js(run, repo, "next")["title"] == "api task"
    assert js(run, plain, "next")["title"] == "free task"
    assert js(run, plain, "next", "--repo", "other/repo")["title"] == "other task"
    # next does not include repo-less tasks inside a repo
    run("complete", ids["api"], cwd=plain)
    run("complete", ids["both"], cwd=plain)
    assert js(run, repo, "next") is None
    assert ok(run, repo, "next") == ""
    assert js(run, plain, "next", "--all-repos")["title"] == "other task"


def test_search_scope(world, run):
    repo, plain, _ = world
    assert titles(js(run, repo, "search", "task")) == {"api task", "both task", "free task"}
    assert titles(js(run, plain, "search", "task")) == {"free task"}
    assert titles(js(run, plain, "search", "task", "--repo", "api")) == {
        "api task",
        "both task",
        "free task",
    }
    assert len(js(run, plain, "search", "task", "--all-repos")) == 4


def test_index_scope(world, run):
    repo, plain, _ = world
    assert ok(run, repo, "index").splitlines()[0] == "repo billing/api"
    assert ok(run, plain, "index").splitlines()[0] == "no repo"
    assert ok(run, plain, "index", "--all-repos").splitlines()[0] == "all repos"
    assert ok(run, plain, "index", "--repo", "other/repo").splitlines()[0] == "repo other/repo"
    assert len(js(run, plain, "index", "--all-repos")["tasks"]) == 4
    assert len(js(run, repo, "index")["tasks"]) == 3
    assert len(js(run, plain, "index")["tasks"]) == 1


# --- next ---


def test_next_order_and_tiebreak(home, plain, run):
    a, b, c = (mk(run, plain, t) for t in ("a", "b", "c"))
    set_created(a, "2026-01-02T00:00:00Z")
    set_created(b, "2026-01-01T00:00:00Z")
    set_created(c, "2026-01-01T00:00:00Z")
    first = min(b, c)
    assert js(run, plain, "next")["id"] == first
    run("complete", first, cwd=plain)
    assert js(run, plain, "next")["id"] == max(b, c)


def test_next_body_and_parent_summary(home, plain, run, tmp_path):
    p = mk(run, plain, "Parent")
    body = tmp_path / "b.md"
    body.write_text("line1\nline2\n", encoding="utf-8")
    c = mk(run, plain, "Child", "--parent", p, "--file", str(body))
    task = js(run, plain, "next")
    assert task["id"] == c
    assert task["body"] == "line1\nline2\n"
    assert task["parent_summary"] == {"id": p, "title": "Parent", "status": "todo"}
    text = ok(run, plain, "next")
    assert "title: Child" in text and "line1\nline2" in text and "Parent" in text
    assert js(run, plain, "show", c)["status"] == "todo"  # plain next only reads


def test_next_nothing_ready(home, plain, run):
    assert ok(run, plain, "next") == ""
    assert js(run, plain, "next") is None


def test_next_start(home, plain, run):
    a = mk(run, plain, "a")
    task = js(run, plain, "next", "--start")
    assert task["id"] == a and task["status"] == "doing"
    assert js(run, plain, "show", a)["status"] == "doing"
    assert js(run, plain, "next", "--start") is None


# --- ls, show ---


def test_ls_filters_and_format(home, plain, run):
    a = mk(run, plain, "alpha", "--tag", "t1")
    b = mk(run, plain, "beta")
    c = mk(run, plain, "gamma")
    run("dep", b, a, cwd=plain)
    run("set", c, "status", "doing", cwd=plain)
    run("complete", a, cwd=plain)
    assert [t["id"] for t in js(run, plain, "ls", "--status", "doing")] == [c]
    assert js(run, plain, "ls", "--tag", "t1") == []
    listed = js(run, plain, "ls", "--archived")
    assert {t["id"] for t in listed} == {a, b, c}
    assert listed[-1]["id"] == a  # archived last
    lines = {line.split()[0]: line for line in ok(run, plain, "ls", "--archived").splitlines()}
    assert "done" in lines[a] and "ready" in lines[b] and "doing" in lines[c]
    assert "ready" not in lines[c] and lines[c].endswith("gamma")
    assert js(run, plain, "ls", "--archived", "--tag", "t1")[0]["id"] == a


def test_show_text(home, plain, run):
    a = mk(run, plain, "Title", "--tag", "x")
    run("note", a, "hello", cwd=plain)
    text = ok(run, plain, "show", a)
    head, _, body = text.partition("\n\n")
    assert f"id: {a}" in head and "tags: x" in head and "title: Title" in head
    assert "branch" not in head and "outcome" not in head
    assert body.strip().endswith("hello")


# --- search ---


def test_search_ranks_title_over_body(home, plain, run, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("the widget is mentioned here\n", encoding="utf-8")
    in_body = mk(run, plain, "unrelated", "--file", str(body))
    in_title = mk(
        run,
        plain,
        "widget",
    )
    assert [t["id"] for t in js(run, plain, "search", "widget")] == [in_title, in_body]


def test_search_quoting_and_archived(home, plain, run):
    a = mk(run, plain, 'say "hi" now')
    b = mk(run, plain, "this AND that")
    c = mk(run, plain, "fix -x flag")
    assert [t["id"] for t in js(run, plain, "search", '"hi"')] == [a]
    assert [t["id"] for t in js(run, plain, "search", "AND")] == [b]
    assert [t["id"] for t in json.loads(run("search", "--json", "--", "-x", cwd=plain).stdout)] == [
        c
    ]
    assert [t["id"] for t in js(run, plain, "search", "this that")] == [b]
    assert [t["id"] for t in js(run, plain, "search", "OR NOT")] == []
    run("complete", c, cwd=plain)
    assert js(run, plain, "search", "flag") == []
    assert [t["id"] for t in js(run, plain, "search", "flag", "--archived")] == [c]


# --- index ---


def test_index_format(home, plain, run):
    d = mk(run, plain, "Doing one")
    r = mk(run, plain, "Review one")
    ready = mk(run, plain, "Ready one")
    w = mk(run, plain, "Blocked one")
    x = mk(run, plain, "Waiting one", "--dep", ready)
    run("set", d, "status", "doing", cwd=plain)
    run("set", r, "status", "review", cwd=plain)
    run("set", w, "status", "blocked", cwd=plain)
    lines = ok(run, plain, "index").splitlines()
    assert lines[0] == "no repo"
    assert lines[1].split(None, 2) == ["doing", d, "Doing one"]
    assert lines[2].split(None, 2) == ["review", r, "Review one"]
    assert lines[3].split(None, 2)[:2] == ["ready", ready]
    assert lines[4:] == ["  +2 waiting"]
    assert x not in "".join(lines)


def test_index_cap_and_empty(home, plain, run):
    assert ok(run, plain, "index").strip() == "no repo"
    for i in range(5):
        mk(run, plain, f"t{i}")
    lines = ok(run, plain, "index", "--limit", "2").splitlines()
    assert len(lines) == 4 and lines[-1] == "  +3 more"
    data = js(run, plain, "index", "--limit", "2")
    assert data["more"] == 3 and data["waiting"] == 0 and len(data["tasks"]) == 2


def test_index_never_fails(home, plain, run, monkeypatch):
    from docket import tasks

    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(tasks, "index", boom)
    result = run("index", cwd=plain)
    assert result.exit_code == 0 and result.stdout == ""
    assert result.stderr.count("\n") == 1 and "kaput" in result.stderr
