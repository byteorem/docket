import re
from pathlib import Path

import pytest

from docket import tasks

TS = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


def task_file(store: Path, task_id: str) -> Path:
    return store / "tasks" / f"{task_id}.md"


def test_create_writes_the_spec_format(store, mk, ok):
    dep_id = mk("Dependency")
    task_id = mk("Add CSV export", "--dep", dep_id, "--set", "branch=feat/csv", stdin="Body\n")
    raw = task_file(store, task_id).read_bytes()
    assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert re.fullmatch(
        "---\n"
        f'id: "{task_id}"\n'
        "title: Add CSV export\n"
        "status: open\n"
        f"created: {TS}\n"
        f'deps: \\["{dep_id}"\\]\n'
        "branch: feat/csv\n"
        "---\n"
        "\n"
        "Body\n",
        raw.decode(),
    )


def test_create_without_body_ends_at_the_fence(store, mk):
    task_id = mk("No body")
    assert task_file(store, task_id).read_text().endswith("---\n")


def test_create_body_from_file(store, mk, tmp_path):
    body = tmp_path / "plan.md"
    body.write_text("# Plan\n\n- step\n", encoding="utf-8")
    task_id = mk("Planned", "--file", str(body))
    assert task_file(store, task_id).read_text().endswith("---\n\n# Plan\n\n- step\n")


def test_create_with_missing_file_fails(store, fail):
    fail("create", "Planned", "--file", "nope.md")


@pytest.mark.parametrize("task_id", ["1234", "1234.1", "0e12", "true"])
def test_ids_are_always_quoted_and_stay_strings(task_id):
    meta = {"id": task_id, "title": "t", "status": "open", "created": "x", "deps": [task_id]}
    text = tasks.dump(meta).decode()
    assert f'id: "{task_id}"' in text
    assert f'deps: ["{task_id}"]' in text
    parsed = tasks.split(tasks.dump(meta))[0]
    assert parsed["id"] == task_id and parsed["deps"] == [task_id]


def test_timestamps_round_trip_as_plain_strings(store, mk, ok):
    task_id = mk("Stamped")
    created = ok("show", task_id)["created"]
    assert isinstance(created, str) and re.fullmatch(TS, created)
    assert f"created: {created}\n" in task_file(store, task_id).read_text()


def test_hand_edited_file_round_trips(store, mk, ok):
    task_id = mk("Original")
    path = task_file(store, task_id)
    body = b"\r\nBody with CRLF\r\n\r\n- [ ] item  \r\ntrailing"
    path.write_bytes(
        b"\xef\xbb\xbf---\r\n"
        b"verdict: local-verified\r\n"
        b'id: "' + task_id.encode() + b'"\r\n'
        b"issue: JIRA-118\r\n"
        b"title: Original\r\n"
        b"status: open\r\n"
        b"created: 2026-10-11T23:08:00Z\r\n"
        b"due: 2026-10-20\r\n"
        b"---\r\n" + body
    )
    ok("set", task_id, "title", "Renamed")
    ok("set", task_id, "branch", "feat/x")

    raw = path.read_bytes()
    head, _, rest = raw.partition(b"---\n")[2].partition(b"---\n")
    assert head.decode().splitlines() == [
        f'id: "{task_id}"',
        "title: Renamed",
        "status: open",
        "created: 2026-10-11T23:08:00Z",
        "verdict: local-verified",
        "issue: JIRA-118",
        "due: 2026-10-20",
        "branch: feat/x",
    ]
    # The original closing fence was CRLF; everything after it is the body, kept byte for byte.
    assert raw.endswith(body)


def test_set_stores_strings(store, ok, mk):
    task_id = mk("Task")
    task = ok("set", task_id, "priority", "1")["task"]
    assert task["priority"] == "1"
    assert "priority: '1'" in task_file(store, task_id).read_text()


def test_set_keeps_literal_newlines_and_backslash_n(store, ok, mk):
    task_id = mk("Task")
    ok("set", task_id, "summary", "line one\nline two \\n")
    assert ok("show", task_id)["summary"] == "line one\nline two \\n"


def test_set_title_and_status(store, ok, mk):
    task_id = mk("Task")
    ok("set", task_id, "title", "New title")
    task = ok("set", task_id, "status", "in_progress")["task"]
    assert (task["title"], task["status"], task["state"]) == ("New title", "in_progress", "ready")


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("id", "abcd", "set by docket"),
        ("created", "x", "set by docket"),
        ("closed_at", "x", "set by docket"),
        ("deps", "x", "docket dep"),
        ("state", "x", "reserved"),
        ("bad key", "x", "invalid field name"),
        ("title", "", "one non-empty line"),
        ("title", "two\nlines", "one non-empty line"),
        ("status", " ", "one non-empty line"),
    ],
)
def test_set_rejects(store, fail, mk, key, value, message):
    task_id = mk("Task")
    assert message in fail("set", task_id, key, value)


def test_unset_removes_a_user_field(store, ok, mk):
    task_id = mk("Task", "--set", "branch=x")
    task = ok("unset", task_id, "branch")["task"]
    assert "branch" not in task
    assert "branch" not in task_file(store, task_id).read_text()


@pytest.mark.parametrize("key, message", [("title", "required"), ("nope", "no field 'nope'")])
def test_unset_rejects(store, fail, mk, key, message):
    assert message in fail("unset", mk("Task"), key)


def test_add_and_remove_list_values(store, ok, mk):
    task_id = mk("Task")
    ok("add", task_id, "tags", "api")
    assert ok("add", task_id, "tags", "multi word")["task"]["tags"] == ["api", "multi word"]
    assert "tags: [api, multi word]" in task_file(store, task_id).read_text()
    assert ok("remove", task_id, "tags", "api")["task"]["tags"] == ["multi word"]
    assert "tags" not in ok("remove", task_id, "tags", "multi word")["task"]


def test_add_and_remove_reject(store, ok, fail, mk):
    task_id = mk("Task", "--set", "branch=x")
    assert "not a list" in fail("add", task_id, "branch", "y")
    ok("add", task_id, "tags", "a")
    assert "already contains" in fail("add", task_id, "tags", "a")
    assert "does not contain" in fail("remove", task_id, "tags", "b")
    assert "no list field" in fail("remove", task_id, "branch", "x")
    assert "docket dep" in fail("add", task_id, "deps", "x")


def test_create_set_rejects_core_fields(store, fail):
    assert "required" in fail("create", "Task", "--set", "status=x")


def test_create_set_without_equals_is_a_usage_error(store, run):
    result = run("create", "Task", "--set", "branch", "--json")
    assert result.code == 2 and result.stdout == ""


def test_note_appends_timestamped_text_under_notes(store, ok, mk):
    task_id = mk("Task", stdin="Body")
    ok("note", task_id, "First")
    ok("note", task_id, stdin="From stdin\nsecond line\n")
    text = task_file(store, task_id).read_text()
    assert re.fullmatch(
        f"(?s)---\n.*---\n\nBody\n\n## Notes\n\n\\*\\*{TS}\\*\\* First\n\n"
        f"\\*\\*{TS}\\*\\* From stdin\nsecond line\n",
        text,
    )


def test_note_stays_out_of_the_last_section(store, ok, mk, tmp_path):
    task_id = mk("Task", stdin="## Plan\n\n- step")
    ok("note", task_id, "logged")
    plan = tmp_path / "plan.md"
    plan.write_text("- new step\n", encoding="utf-8")
    ok("body", task_id, "--section", "Plan", "--file", str(plan))
    assert re.fullmatch(
        f"\n## Plan\n\n- new step\n\n## Notes\n\n\\*\\*{TS}\\*\\* logged\n", body_of(store, task_id)
    )


def test_note_reuses_an_existing_notes_section(store, ok, mk):
    task_id = mk("Task", stdin="## notes\n\nby hand\n\n## Plan\n\n- step")
    ok("note", task_id, "logged")
    assert re.fullmatch(
        f"\n## notes\n\nby hand\n\n\\*\\*{TS}\\*\\* logged\n\n## Plan\n\n- step\n",
        body_of(store, task_id),
    )


def test_note_needs_text(store, fail, mk):
    assert "empty" in fail("note", mk("Task"), "  ")


def body_of(store: Path, task_id: str) -> str:
    return task_file(store, task_id).read_text().split("---\n", 2)[2]


def test_body_replaces_appends_and_swaps(store, ok, mk, tmp_path):
    task_id = mk("Task", stdin="Old body")
    head = task_file(store, task_id).read_bytes().partition(b"---\n\n")[0]
    ok("body", task_id, stdin="New body\n\n")
    assert body_of(store, task_id) == "\nNew body\n"
    ok("body", task_id, "--append", "More")
    assert body_of(store, task_id) == "\nNew body\n\nMore\n"
    ok("body", task_id, "--replace", "New body", "Fresh body")
    assert body_of(store, task_id) == "\nFresh body\n\nMore\n"
    plan = tmp_path / "plan.md"
    plan.write_text("From file\n", encoding="utf-8")
    ok("body", task_id, "--file", str(plan))
    assert body_of(store, task_id) == "\nFrom file\n"
    assert task_file(store, task_id).read_bytes().startswith(head)


def test_body_replace_needs_exactly_one_match(store, ok, fail, mk):
    task_id = mk("Task", stdin="a b a")
    assert "2 times" in fail("body", task_id, "--replace", "a", "c")
    assert "not found" in fail("body", task_id, "--replace", "z", "c")
    ok("body", task_id, "--replace", " b", "")
    assert body_of(store, task_id) == "\na a\n"


SECTIONED = """\
Intro

## Plan

- old step

### Detail

deep

## Notes

note
"""


def test_body_sections(store, ok, mk):
    task_id = mk("Task", stdin=SECTIONED)
    ok("body", task_id, "--section", "plan", "--append", "- new step")
    ok("body", task_id, "--section", "Detail", "--replace", "deep", "deeper")
    ok("body", task_id, "--section", "Notes", stdin="replaced")
    assert body_of(store, task_id) == (
        "\nIntro\n\n## Plan\n\n- old step\n\n### Detail\n\ndeeper\n\n- new step\n\n"
        "## Notes\n\nreplaced\n"
    )


def test_body_section_name_may_include_the_heading_marks(store, ok, mk):
    task_id = mk("Task", stdin="## Plan\n\n- step")
    ok("body", task_id, "--section", "## Plan", "--append", "- next")
    assert body_of(store, task_id) == "\n## Plan\n\n- step\n\n- next\n"


def test_body_adds_a_missing_section_but_replace_needs_it(store, ok, fail, mk):
    task_id = mk("Task", stdin="Intro")
    assert "no section 'Plan'" in fail("body", task_id, "--section", "Plan", "--replace", "a", "b")
    ok("body", task_id, "--section", "Plan", "--append", "- step")
    assert body_of(store, task_id) == "\nIntro\n\n## Plan\n\n- step\n"


def test_body_section_skips_headings_in_code_fences(store, ok, mk):
    task_id = mk("Task", stdin="## Plan\n\n````md\n## Notes\n```\n````\n")
    ok("body", task_id, "--section", "Notes", "--append", "real")
    ok("body", task_id, "--section", "Plan", "--append", "step")
    assert body_of(store, task_id) == (
        "\n## Plan\n\n````md\n## Notes\n```\n````\n\nstep\n\n## Notes\n\nreal\n"
    )


def test_body_rejects_duplicate_sections(store, fail, mk):
    task_id = mk("Task", stdin="## Plan\n\na\n\n## plan ##\n\nb")
    message = fail("body", task_id, "--section", "Plan", "--append", "x")
    assert "appears 2 times (lines 8, 12)" in message


def test_body_keeps_crlf_line_endings(store, ok, mk):
    task_id = mk("Task")
    path = task_file(store, task_id)
    path.write_bytes(path.read_bytes() + b"\r\n## Plan\r\n\r\nold\r\n")
    ok("body", task_id, "--section", "Plan", "--append", "one\ntwo")
    assert path.read_bytes().endswith(b"\r\n## Plan\r\n\r\nold\r\n\r\none\r\ntwo\r\n")


def test_show_section_prints_only_that_section(store, ok, run, fail, mk):
    task_id = mk("Task", stdin=SECTIONED)
    assert ok("show", task_id, "--section", "## plan") == {
        "id": task_id,
        "section": "plan",
        "text": "- old step\n\n### Detail\n\ndeep",
    }
    assert run("show", task_id, "--section", "Notes").stdout == "note\n"
    assert "no section 'Missing'" in fail("show", task_id, "--section", "Missing")


@pytest.mark.parametrize(
    "args", [("show",), ("show", "--section", "Plan"), ("body", "--append", "x")]
)
def test_body_that_is_not_utf8_is_a_one_line_error(store, fail, mk, args):
    task_id = mk("Task", stdin="## Plan\n\nstep")
    path = task_file(store, task_id)
    path.write_bytes(path.read_bytes() + b"caf\xe9\n")
    before = path.read_bytes()
    assert "not valid UTF-8 on line 11" in fail(args[0], task_id, *args[1:])
    assert path.read_bytes() == before


def test_body_rejects_empty_text_and_mixed_modes(store, run, fail, mk):
    task_id = mk("Task", stdin="Body")
    assert "empty" in fail("body", task_id)
    assert "empty" in fail("body", task_id, "--replace", "", "x")
    result = run("body", task_id, "--append", "x", "--replace", "a", "b", "--json")
    assert result.code == 2 and result.stdout == ""


def test_show_text_prints_the_file_then_sections(store, run, mk):
    parent = mk("Parent")
    child = mk("Child", "--parent", parent, stdin="Child body")
    result = run("show", child)
    assert result.code == 0
    file_text = task_file(store, child).read_text()
    assert result.stdout.startswith(file_text.rstrip("\n") + "\n\nParent:\n  " + parent)


def test_path_prints_the_absolute_path(store, run, mk):
    task_id = mk("Task")
    assert run("path", task_id[:3]).stdout.strip() == str(task_file(store, task_id))


def test_invalid_file_is_skipped_with_a_warning(store, run, ok, fail, mk):
    good = mk("Good")
    (store / "tasks" / "zzzz.md").write_text("no frontmatter\n", encoding="utf-8")
    result = run("ls", "--json")
    assert [t["id"] for t in result.json()] == [good]
    assert "skipped 1 invalid task file" in result.stderr
    assert "fix it by hand" in fail("show", "zzzz")


def test_where_filters_match_strings_and_lists(store, ok, mk):
    a = mk("A", "--set", "branch=main")
    b = mk("B")
    ok("add", b, "tags", "api")
    ok("add", b, "tags", "ui")
    assert [t["id"] for t in ok("ls", "--where", "branch=main")] == [a]
    assert [t["id"] for t in ok("ls", "--where", "tags=ui")] == [b]
    assert ok("ls", "--where", "tags=ui", "--where", "branch=main") == []
    assert {t["id"] for t in ok("ls", "--status", "open")} == {a, b}
