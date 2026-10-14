import json

import pytest
from helpers import fail, js, mk, ok

from docket import db

KEYS = [
    "id",
    "parent",
    "title",
    "body",
    "status",
    "blocked_on",
    "repos",
    "depends_on",
    "discovered_from",
    "tags",
    "branch",
    "pr",
    "issue",
    "links",
    "created",
    "outcome",
    "archived",
]


@pytest.fixture
def plain(home, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    return d


# --- add, IDs, shape ---


def test_add_shape_and_defaults(home, plain, run):
    task = js(run, plain, "add", "Hello", "--tag", "b", "--tag", "a")
    assert list(task) == KEYS
    assert len(task["id"]) == 4
    assert task["parent"] is None
    assert task["status"] == "todo"
    assert task["tags"] == ["a", "b"]
    assert task["repos"] == []
    assert task["outcome"] is None and task["archived"] is None
    assert ok(run, plain, "add", "Two", "--no-repo").strip().endswith("repos: (none)")


def test_child_ids_are_sequential(home, plain, run):
    p = mk(run, plain, "p")
    c1 = mk(run, plain, "c1", "--parent", p)
    c2 = mk(run, plain, "c2", "--parent", p)
    g = mk(run, plain, "g", "--parent", c1)
    assert (c1, c2, g) == (f"{p}.1", f"{p}.2", f"{p}.1.1")
    assert js(run, plain, "show", g)["parent"] == c1
    run("complete", g, cwd=plain)
    run("complete", c2, cwd=plain)
    assert mk(run, plain, "c3", "--parent", p) == f"{p}.3"


def test_add_repo_defaults_and_flags(home, git_repo, plain, run):
    repo = git_repo("a", origin="https://h/billing/api.git")
    assert js(run, repo, "add", "x")["repos"] == ["billing/api"]
    assert js(run, repo, "add", "x", "--no-repo")["repos"] == []
    assert js(run, repo, "add", "x", "--repo", "o/one", "--repo", "o/two")["repos"] == [
        "o/one",
        "o/two",
    ]
    assert js(run, repo, "add", "x", "--repo", "api")["repos"] == ["billing/api"]
    assert js(run, plain, "add", "x")["repos"] == []
    assert run("add", "x", "--repo", "o/r", "--no-repo", cwd=plain).exit_code == 2
    assert "repos: billing/api" in ok(run, repo, "add", "x")


def test_add_no_origin_warns(home, git_repo, run):
    repo = git_repo("folder")
    result = run("add", "x", cwd=repo)
    assert "no origin remote; using folder name 'folder'" in result.stderr
    assert "repos: folder" in result.stdout


def test_add_from_and_dep(home, plain, run):
    a = mk(run, plain, "a")
    b = mk(run, plain, "b", "--from", a[:3], "--dep", a)
    task = js(run, plain, "show", b)
    assert task["discovered_from"] == a
    assert task["depends_on"] == [a]
    run("complete", a, cwd=plain)
    assert js(run, plain, "add", "c", "--from", a)["discovered_from"] == a
    assert "no task" in fail(run, plain, "add", "d", "--from", "zzzz")
    assert "no task" in fail(run, plain, "add", "d", "--dep", "zzzz")
    assert "archived" in fail(run, plain, "add", "d", "--dep", a)


def test_failed_add_rolls_back(home, plain, run):
    fail(run, plain, "add", "bad", "--dep", "zzzz")
    assert js(run, plain, "ls") == []


def test_add_requires_open_parent(home, plain, run):
    p = mk(run, plain, "p")
    run("complete", p, cwd=plain)
    assert "archived" in fail(run, plain, "add", "c", "--parent", p)
    assert "no task" in fail(run, plain, "add", "c", "--parent", "zzzz")


# --- prefix resolution ---


def _insert(*ids):
    conn = db.connect()
    for i in ids:
        conn.execute(
            "INSERT INTO tasks(id, parent_id, title, status, created) "
            "VALUES (?, ?, 't', 'todo', 'x')",
            (i, i.rsplit(".", 1)[0] if "." in i else None),
        )
    conn.close()


def test_exact_id_beats_prefix(home, plain, run):
    _insert("a3f8", "a3f8.1", "a3k2")
    assert js(run, plain, "show", "a3f8")["id"] == "a3f8"
    assert js(run, plain, "show", "a3f8.")["id"] == "a3f8.1"
    assert js(run, plain, "show", "a3k")["id"] == "a3k2"


def test_ambiguous_and_unknown_prefix(home, plain, run):
    _insert("a3f8", "a3k2")
    err = fail(run, plain, "show", "a3")
    assert "ambiguous" in err and "a3f8" in err and "a3k2" in err
    assert "no task matches" in fail(run, plain, "show", "zz")


# --- dependencies ---


def test_dep_add_remove(home, plain, run):
    a, b = mk(run, plain, "a"), mk(run, plain, "b")
    assert js(run, plain, "dep", a, b)["depends_on"] == [b]
    assert js(run, plain, "dep", a, b)["depends_on"] == [b]  # no-op success
    assert js(run, plain, "undep", a, b)["depends_on"] == []
    assert "does not depend" in fail(run, plain, "undep", a, b)


def test_dep_rejects_missing_and_archived(home, plain, run):
    a, b = mk(run, plain, "a"), mk(run, plain, "b")
    assert "no task" in fail(run, plain, "dep", a, "zzzz")
    run("complete", b, cwd=plain)
    assert "archived" in fail(run, plain, "dep", a, b)


# --- cycles ---


def test_cycle_self(home, plain, run):
    a = mk(run, plain, "a")
    assert "cycle" in fail(run, plain, "dep", a, a)
    assert js(run, plain, "show", a)["depends_on"] == []


def test_cycle_ancestor_and_descendant(home, plain, run):
    p = mk(run, plain, "p")
    c = mk(run, plain, "c", "--parent", p)
    g = mk(run, plain, "g", "--parent", c)
    assert "cycle" in fail(run, plain, "dep", c, p)
    assert "cycle" in fail(run, plain, "dep", g, p)
    assert "cycle" in fail(run, plain, "dep", p, c)
    assert "cycle" in fail(run, plain, "dep", p, g)


def test_cycle_inherited_through_ancestor_dep(home, plain, run):
    x, p = mk(run, plain, "x"), mk(run, plain, "p")
    c = mk(run, plain, "c", "--parent", p)
    run("dep", p, x, cwd=plain)
    # c waits on x through p, so x may not wait on c (or on p itself).
    assert "cycle" in fail(run, plain, "dep", x, c)
    assert "cycle" in fail(run, plain, "dep", x, p)


def test_cycle_indirect(home, plain, run):
    a, b, c = (mk(run, plain, t) for t in "abc")
    run("dep", a, b, cwd=plain)
    run("dep", b, c, cwd=plain)
    assert "cycle" in fail(run, plain, "dep", c, a)
    assert js(run, plain, "show", c)["depends_on"] == []


def test_cycle_via_descendant_of_dependent(home, plain, run):
    # a's child d inherits a's deps; b depends on d, so a may not depend on b.
    a, b = mk(run, plain, "a"), mk(run, plain, "b")
    d = mk(run, plain, "d", "--parent", a)
    run("dep", b, d, cwd=plain)
    assert "cycle" in fail(run, plain, "dep", a, b)


def test_cycle_on_reopen(home, plain, run):
    p, x = mk(run, plain, "p"), mk(run, plain, "x")
    c = mk(run, plain, "c", "--parent", p)
    run("dep", c, x, cwd=plain)
    run("complete", c, cwd=plain)
    assert js(run, plain, "dep", x, p)["depends_on"] == [p]  # allowed: c is archived
    assert "cycle" in fail(run, plain, "reopen", c)
    task = js(run, plain, "show", c)
    assert task["archived"] is not None and task["status"] is None


# --- lifecycle rules ---


def test_archive_and_reopen_fields(home, plain, run):
    a = mk(run, plain, "a")
    run("set", a, "status", "blocked", cwd=plain)
    run("set", a, "blocked_on", "vendor", cwd=plain)
    done = js(run, plain, "complete", a)
    assert done["status"] is None and done["blocked_on"] is None
    assert done["outcome"] == "done" and done["archived"]
    back = js(run, plain, "reopen", a)
    assert back["status"] == "doing" and back["outcome"] is None and back["archived"] is None
    canceled = js(run, plain, "cancel", a)
    assert canceled["outcome"] == "canceled"


def test_archived_and_open_errors(home, plain, run):
    a = mk(run, plain, "a")
    assert "not archived" in fail(run, plain, "reopen", a)
    run("complete", a, cwd=plain)
    assert "archived" in fail(run, plain, "complete", a)
    assert "archived" in fail(run, plain, "cancel", a)


def test_parent_needs_closed_children(home, plain, run):
    p = mk(run, plain, "p")
    c = mk(run, plain, "c", "--parent", p)
    assert c in fail(run, plain, "complete", p)
    assert c in fail(run, plain, "cancel", p)
    run("complete", c, cwd=plain)
    run("complete", p, cwd=plain)
    assert "parent" in fail(run, plain, "reopen", c)
    run("reopen", p, cwd=plain)
    assert js(run, plain, "reopen", c)["status"] == "doing"


def test_cancel_blocked_by_open_dependents(home, plain, run):
    a, b, c = mk(run, plain, "a"), mk(run, plain, "b"), mk(run, plain, "c")
    run("dep", b, a, cwd=plain)
    run("dep", c, a, cwd=plain)
    err = fail(run, plain, "cancel", a)
    assert b in err and c in err
    run("complete", b, cwd=plain)
    run("complete", c, cwd=plain)
    assert js(run, plain, "cancel", a)["outcome"] == "canceled"


def test_complete_allowed_with_open_dependents(home, plain, run):
    a, b = mk(run, plain, "a"), mk(run, plain, "b")
    run("dep", b, a, cwd=plain)
    assert js(run, plain, "complete", a)["outcome"] == "done"


# --- readiness ---


def ready_ids(run, cwd):
    return {t["id"] for t in js(run, cwd, "ls") if t["ready"]}


def test_readiness_deps(home, plain, run):
    a, b = mk(run, plain, "a"), mk(run, plain, "b")
    run("dep", b, a, cwd=plain)
    assert ready_ids(run, plain) == {a}
    run("complete", a, cwd=plain)
    assert ready_ids(run, plain) == {b}


def test_readiness_ancestor_deps_and_open_children(home, plain, run):
    x, p = mk(run, plain, "x"), mk(run, plain, "p")
    c = mk(run, plain, "c", "--parent", p)
    run("dep", p, x, cwd=plain)
    assert ready_ids(run, plain) == {x}  # p has an open child; c waits through p
    run("complete", x, cwd=plain)
    assert ready_ids(run, plain) == {c}
    run("complete", c, cwd=plain)
    assert ready_ids(run, plain) == {p}


def test_readiness_needs_todo(home, plain, run):
    a = mk(run, plain, "a")
    for status in ("doing", "review", "blocked"):
        run("set", a, "status", status, cwd=plain)
        assert ready_ids(run, plain) == set()
    run("set", a, "status", "todo", cwd=plain)
    assert ready_ids(run, plain) == {a}


# --- set ---


def test_set_scalars(home, plain, run):
    a = mk(run, plain, "a")
    assert js(run, plain, "set", a, "title", "New")["title"] == "New"
    assert js(run, plain, "set", a, "branch", "feat/x")["branch"] == "feat/x"
    assert js(run, plain, "set", a, "pr", "12")["pr"] == "12"
    assert js(run, plain, "set", a, "issue", "ABC-1")["issue"] == "ABC-1"
    assert js(run, plain, "set", a, "branch", "")["branch"] is None
    assert "title is empty" in fail(run, plain, "set", a, "title", " ")
    assert "invalid status" in fail(run, plain, "set", a, "status", "nope")


def test_set_status_and_blocked_on(home, plain, run):
    a = mk(run, plain, "a")
    assert "not blocked" in fail(run, plain, "set", a, "blocked_on", "x")
    run("set", a, "status", "blocked", cwd=plain)
    assert js(run, plain, "set", a, "blocked_on", "vendor")["blocked_on"] == "vendor"
    task = js(run, plain, "set", a, "status", "review")
    assert task["status"] == "review" and task["blocked_on"] is None


def test_set_archived_rules(home, plain, run):
    a = mk(run, plain, "a")
    run("complete", a, cwd=plain)
    assert "archived" in fail(run, plain, "set", a, "status", "doing")
    assert "archived" in fail(run, plain, "set", a, "blocked_on", "x")
    assert js(run, plain, "set", a, "title", "T")["title"] == "T"
    assert js(run, plain, "set", a, "branch", "b")["branch"] == "b"
    assert js(run, plain, "set", a, "tags", "x,y")["tags"] == ["x", "y"]
    assert js(run, plain, "set", a, "links", "https://e.com")["links"] == ["https://e.com"]


def test_set_list_fields(home, plain, run):
    a = mk(run, plain, "a", "--tag", "old")
    assert js(run, plain, "set", a, "tags", "x, y,x")["tags"] == ["x", "y"]
    assert js(run, plain, "set", a, "tags", "--add", "z", "--add", "w")["tags"] == [
        "w",
        "x",
        "y",
        "z",
    ]
    assert js(run, plain, "set", a, "tags", "--remove", "x", "--remove", "y")["tags"] == [
        "w",
        "z",
    ]
    assert js(run, plain, "set", a, "tags", "")["tags"] == []
    assert js(run, plain, "set", a, "links", "u1,u2")["links"] == ["u1", "u2"]
    assert js(run, plain, "set", a, "links", "--remove", "u1")["links"] == ["u2"]


def test_set_repos_resolve_keys(home, plain, run):
    a = mk(run, plain, "a", "--repo", "billing/api")
    assert js(run, plain, "set", a, "repos", "--add", "api")["repos"] == ["billing/api"]
    assert js(run, plain, "set", a, "repos", "o/x,o/y")["repos"] == ["o/x", "o/y"]
    assert "no known repo" in fail(run, plain, "set", a, "repos", "--add", "nowhere")
    assert js(run, plain, "set", a, "repos", "")["repos"] == []


def test_set_usage_errors(home, plain, run):
    a = mk(run, plain, "a")
    assert run("set", a, "tags", cwd=plain).exit_code == 2
    assert run("set", a, "title", cwd=plain).exit_code == 2
    assert run("set", a, "bogus", "v", cwd=plain).exit_code == 2
    assert run("set", a, "title", "v", "--add", "x", cwd=plain).exit_code == 2


def test_json_error_has_empty_stdout(home, plain, run):
    result = run("show", "zzzz", "--json", cwd=plain)
    assert result.exit_code == 1 and result.stdout == ""
    assert json.loads(run("add", "x", "--json", cwd=plain).stdout)["title"] == "x"
