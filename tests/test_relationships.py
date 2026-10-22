import pytest


def write_task(store, task_id: str, *extra: str, status: str = "open") -> None:
    """Write a task file by hand, for IDs and states the CLI would not produce."""
    lines = [
        "---",
        f'id: "{task_id}"',
        f"title: Task {task_id}",
        f"status: {status}",
        "created: 2026-10-11T23:08:00Z",
        *extra,
        "---",
        "",
    ]
    path = store / "tasks" / f"{task_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# --- IDs ---


def test_top_level_ids_are_four_lowercase_characters(store, mk):
    ids = {mk(f"Task {i}") for i in range(5)}
    assert len(ids) == 5
    assert all(len(i) == 4 and i.isalnum() and i == i.lower() for i in ids)


def test_prefix_resolution(store, ok, fail):
    write_task(store, "a3f8")
    write_task(store, "a3f8.1")
    write_task(store, "a3f8.1.1")
    write_task(store, "a3k2")
    assert ok("show", "a3f8")["id"] == "a3f8"
    assert ok("show", "a3k")["id"] == "a3k2"
    assert ok("show", "a3f")["id"] == "a3f8"
    assert ok("show", "a3f8.")["id"] == "a3f8.1"
    assert "ambiguous ID 'a3': a3f8, a3f8.1, a3f8.1.1, a3k2" in fail("show", "a3")
    assert "no task 'zz'" in fail("show", "zz")


def test_subtask_ids_count_past_closed_children(store, ok, mk):
    parent = mk("Parent")
    first = mk("One", "--parent", parent)
    second = mk("Two", "--parent", parent)
    assert (first, second) == (f"{parent}.1", f"{parent}.2")
    ok("close", second)
    assert mk("Three", "--parent", parent) == f"{parent}.3"
    assert mk("Nested", "--parent", first) == f"{parent}.1.1"


def test_subtask_numbers_sort_numerically(store, ok, mk):
    write_task(store, "a3f8")
    write_task(store, "a3f8.9")
    write_task(store, "a3f8.10")
    assert mk("Next", "--parent", "a3f8") == "a3f8.11"
    assert [t["id"] for t in ok("ls", "--tree")] == ["a3f8", "a3f8.9", "a3f8.10", "a3f8.11"]


def test_create_parent_must_be_open(store, ok, fail, mk):
    parent = mk("Parent")
    ok("close", parent)
    assert "closed" in fail("create", "Child", "--parent", parent)


# --- deps and state ---


def test_dep_rules(store, ok, fail, mk):
    a, b, c = mk("A"), mk("B"), mk("C")
    assert "itself" in fail("dep", a, a)
    assert "no task" in fail("dep", a, "zzzz")
    ok("dep", a, b)
    ok("dep", b, c)
    assert f"dependency cycle: {c} -> {a} -> {b} -> {c}" in fail("dep", c, a)
    assert "already depends" in fail("dep", a, b)


def test_states_follow_deps(store, ok, mk):
    dep_id = mk("Dependency")
    task_id = mk("Waiting", "--dep", dep_id)
    ok("set", task_id, "status", "in_progress")
    assert ok("show", task_id)["state"] == "blocked"
    blocked = ok("blocked")
    assert [(t["id"], t["blockers"]) for t in blocked] == [(task_id, [dep_id])]
    assert [t["id"] for t in ok("ready")] == [dep_id]

    ok("close", dep_id)
    task = ok("show", task_id)
    assert (task["state"], task["status"], task["deps"]) == ("ready", "in_progress", [dep_id])
    assert [t["id"] for t in ok("ready")] == [task_id]


def test_missing_dep_blocks_and_can_be_removed(store, ok, mk):
    dep_id = mk("Doomed")
    task_id = mk("Waiting", "--dep", dep_id)
    ok("close", dep_id)
    (store / "tasks" / f"{dep_id}.md").unlink()
    assert ok("show", task_id)["state"] == "blocked"
    assert ok("undep", task_id, dep_id[:2])["task"]["deps"] == []
    assert ok("show", task_id)["state"] == "ready"


def test_undep_rejects_a_dep_that_is_not_there(store, fail, mk):
    assert "does not depend" in fail("undep", mk("A"), mk("B"))


def test_show_relations(store, ok, mk):
    blocker = mk("Blocker")
    parent = mk("Parent", "--dep", blocker)
    child = mk("Child", "--parent", parent)
    waiter = mk("Waiter", "--dep", parent)
    task = ok("show", parent)
    assert task["blockers"] == [blocker]
    assert task["blocking"] == [waiter]
    assert task["subtasks"] == [child]
    assert task["parent"] is None
    assert task["body"] == ""
    assert ok("show", child)["parent"] == parent


# --- close and reopen ---


def test_close_sets_closed_at_and_reopen_removes_it(store, ok, fail, mk):
    task_id = mk("Task")
    task = ok("close", task_id)["task"]
    assert task["state"] == "closed" and "closed_at" in task
    assert "already closed" in fail("close", task_id)
    task = ok("reopen", task_id)["task"]
    assert (task["status"], task["state"]) == ("open", "ready")
    assert "closed_at" not in task
    assert "not closed" in fail("reopen", task_id)


def test_close_status_must_be_a_closed_status(store, fail, mk):
    assert "not a closed status" in fail("close", mk("Task"), "--status", "done")


def test_close_with_open_subtasks_needs_force(store, ok, fail, mk):
    parent = mk("Parent")
    child = mk("Child", "--parent", parent)
    grandchild = mk("Grandchild", "--parent", child)
    assert f"open subtasks: {child}, {grandchild}" in fail("close", parent)
    assert "open subtasks" in fail("set", parent, "status", "closed")
    assert ok("close", parent, "--force")["task"]["state"] == "closed"


def test_reopen_under_a_closed_parent_needs_force(store, ok, fail, mk):
    parent = mk("Parent")
    child = mk("Child", "--parent", parent)
    ok("close", child)
    ok("close", parent)
    assert "parent" in fail("reopen", child)
    assert "parent" in fail("set", child, "status", "open")
    assert ok("reopen", child, "--force")["task"]["state"] == "ready"


def test_open_subtasks_do_not_change_the_parent(store, ok, mk):
    parent = mk("Parent")
    mk("Child", "--parent", parent)
    assert ok("show", parent)["state"] == "ready"


def test_moving_between_closed_statuses_keeps_closed_at(store, ok, mk):
    (store / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (store / "config.yaml").write_text("closed_statuses: [done, wontfix]\n", encoding="utf-8")
    task_id = mk("Task")
    closed_at = ok("close", task_id)["task"]["closed_at"]
    task = ok("close", task_id, "--status", "wontfix")["task"]
    assert (task["status"], task["closed_at"]) == ("wontfix", closed_at)


# --- listing ---


def test_ls_hides_closed_unless_all(store, ok, mk):
    open_id, closed_id = mk("Open"), mk("Closed")
    ok("close", closed_id)
    assert [t["id"] for t in ok("ls")] == [open_id]
    assert {t["id"] for t in ok("ls", "--all")} == {open_id, closed_id}


def test_ls_tree_text(store, run):
    write_task(store, "a3f8")
    write_task(store, "a3f8.1")
    write_task(store, "a3f8.1.1", 'deps: ["m7x2"]')
    write_task(store, "m7x2", status="in_progress")

    def row(label: str, state: str, status: str, title: str) -> str:
        return f"{label:<12}  {state:<7}  {status:<11}  {title}"

    assert run("ls", "--tree").stdout.splitlines() == [
        row("a3f8", "ready", "open", "Task a3f8"),
        row("  a3f8.1", "ready", "open", "Task a3f8.1"),
        row("    a3f8.1.1", "blocked", "open", "Task a3f8.1.1"),
        row("m7x2", "ready", "in_progress", "Task m7x2"),
    ]


def test_tree_nests_under_the_nearest_listed_ancestor(store, ok, run):
    write_task(store, "a3f8")
    write_task(store, "a3f8.1", status="closed")
    write_task(store, "a3f8.1.1")
    lines = run("ls", "--tree").stdout.splitlines()
    assert [line.split()[0] for line in lines] == ["a3f8", "a3f8.1.1"]
    assert lines[1].startswith("  a3f8.1.1")


@pytest.mark.parametrize("command", ["ls", "ready", "blocked"])
def test_list_commands_print_nothing_when_empty(store, run, command):
    result = run(command)
    assert (result.code, result.stdout) == (0, "")
