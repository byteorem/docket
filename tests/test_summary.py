from pathlib import Path

from test_relationships import write_task


def test_index_prints_nothing_without_a_store(store, run):
    result = run("index", "--json")
    assert (result.code, result.stdout, result.stderr) == (0, "", "")


def test_index_text(store, run):
    write_task(store, "k2p9", status="in_progress")
    write_task(store, "k2p9.1")
    write_task(store, "k2p9.2")
    write_task(store, "k2p9.3", status="closed")
    write_task(store, "a3f8")
    write_task(store, "m7x2", 'deps: ["zzzz"]')
    write_task(store, "done", status="closed")
    assert run("index").stdout.splitlines() == [
        f"store {store}",
        "  ready    k2p9  Task k2p9  in_progress  (2 open subtasks)",
        "  ready    a3f8  Task a3f8  open",
        "  +1 blocked",
    ]


def test_index_json_and_limit(store, ok):
    write_task(store, "aaaa", status="review")
    write_task(store, "bbbb", 'deps: ["aaaa"]', status="in_progress")
    write_task(store, "cccc")
    write_task(store, "dddd")
    write_task(store, "eeee", 'deps: ["aaaa"]')
    data = ok("index", "--limit", "2")
    assert data["store"] == str(store)
    assert [(t["id"], t["state"], t["open_subtasks"]) for t in data["underway"]] == [
        ("aaaa", "ready", 0),
        ("bbbb", "blocked", 0),
    ]
    assert data["ready"] == []
    assert (data["blocked"], data["omitted"]) == (1, 2)
    assert {"path", "state", "parent", "title", "status", "created"} <= set(data["underway"][0])


def test_index_always_exits_zero(store, run):
    (store).mkdir(parents=True)
    (store / "config.yaml").write_text("closed_statuses: []\n", encoding="utf-8")
    result = run("index")
    assert result.code == 0 and result.stdout == ""
    assert "closed_statuses" in result.stderr


def test_check_clean_store(store, ok, mk):
    mk("Fine")
    assert ok("check") == {"problems": []}


def test_check_reports_every_kind(store, run, mk):
    tasks = store / "tasks"
    write_task(store, "a3f8", 'deps: ["b4c5"]')
    write_task(store, "b4c5", 'deps: ["a3f8", "gone"]')
    write_task(store, "c6d7.1")
    write_task(store, "e8f9")
    (tasks / "e8f9.md").rename(tasks / "wxyz.md")
    (tasks / "bad1.md").write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8")
    (tasks / "bad2.md").write_text('---\nid: "bad2"\ntitle: no status\n---\n', encoding="utf-8")
    write_task(store, "f0a1")
    with (tasks / "f0a1.md").open("ab") as f:
        f.write(b"\nLatin-1 caf\xe9\n")

    result = run("check", "--json")
    assert result.code == 1
    problems = result.json()["problems"]
    assert {(Path(p["path"]).name, p["kind"]) for p in problems} == {
        ("a3f8.md", "cycle"),
        ("b4c5.md", "missing-dep"),
        ("bad1.md", "invalid"),
        ("bad2.md", "invalid"),
        ("c6d7.1.md", "orphan"),
        ("f0a1.md", "encoding"),
        ("wxyz.md", "id-mismatch"),
    }
    by_kind = {p["kind"]: p["message"] for p in problems}
    assert by_kind["cycle"] == "dependency cycle: a3f8 -> b4c5 -> a3f8"
    assert by_kind["missing-dep"] == "b4c5 depends on missing task gone"
    assert by_kind["encoding"].startswith("f0a1 is not valid UTF-8 on line ")

    text = run("check")
    assert text.code == 1
    assert "orphan: c6d7.1 has no parent c6d7" in text.stdout.splitlines()


def test_unquoted_numeric_id_is_invalid(store, run):
    (store / "tasks").mkdir(parents=True)
    (store / "tasks" / "1234.md").write_text(
        "---\nid: 1234\ntitle: t\nstatus: open\ncreated: x\n---\n", encoding="utf-8"
    )
    [problem] = run("check", "--json").json()["problems"]
    assert (problem["kind"], problem["message"]) == ("invalid", "id 1234 is not a quoted task ID")


def test_write_commands_return_the_task(store, ok, mk):
    task_id = mk("Task")
    for args in (
        ("set", task_id, "branch", "x"),
        ("unset", task_id, "branch"),
        ("add", task_id, "tags", "a"),
        ("remove", task_id, "tags", "a"),
        ("note", task_id, "text"),
        ("close", task_id),
        ("reopen", task_id),
    ):
        data = ok(*args)
        assert set(data) == {"task"}
        assert data["task"]["id"] == task_id
        assert data["task"]["path"].endswith(f"{task_id}.md")


def test_write_command_text_is_one_line(store, run, mk):
    task_id = mk("Fix auth redirect")
    assert run("set", task_id, "status", "in_progress").stdout == (
        f"updated {task_id}  Fix auth redirect\n"
    )


def test_list_json_is_an_array_of_task_objects(store, ok, mk):
    task_id = mk("Task")
    [task] = ok("ls")
    assert {"id", "title", "status", "created", "deps", "path", "state", "parent"} <= set(task)
    assert "body" not in task
    assert task["id"] == task_id


def test_usage_errors_exit_2(store, run):
    for args in (("bogus",), ("show",), ("index", "--limit", "x"), ()):
        result = run(*args)
        assert result.code == 2, args
        assert result.stdout == ""


def test_help(store, run):
    result = run("help")
    assert result.code == 0
    assert "create" in result.stdout and "check" in result.stdout
    assert "--parent" in run("help", "create").stdout
