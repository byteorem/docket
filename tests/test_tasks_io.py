import json
import os
import subprocess
import sys
import textwrap

import pytest
from helpers import fail, js, mk, ok

from docket import db


@pytest.fixture
def plain(home, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    return d


# --- add body ---


def test_add_body_from_file_preserves_newlines(home, plain, run, tmp_path):
    f = tmp_path / "b.md"
    f.write_bytes(b"a\\nb\nc\r\nd\n")
    a = mk(run, plain, "x", "--file", str(f))
    assert js(run, plain, "show", a)["body"] == "a\\nb\nc\nd\n"  # \n escape kept, CRLF -> LF
    assert "cannot read" in fail(run, plain, "add", "y", "--file", str(tmp_path / "missing"))


def test_add_body_via_real_stdin(home, plain):
    env = {**os.environ, "DOCKET_HOME": str(home)}
    proc = subprocess.run(
        [sys.executable, "-m", "docket", "add", "x", "--json"],
        input="piped\nbody",
        cwd=plain,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["body"] == "piped\nbody"


# --- note ---


def test_note_appends(home, plain, run):
    a = mk(run, plain, "x")
    first = js(run, plain, "note", a, "one")["body"]
    assert first.startswith("- 20") and first.endswith("Z one")
    second = js(run, plain, "note", a, "two")["body"]
    lines = second.split("\n")
    assert lines[0] == first and lines[1].endswith("Z two")
    run("complete", a, cwd=plain)
    assert js(run, plain, "note", a, "three")["body"].endswith("Z three")


# --- attach ---


def test_attach_copies_and_links(home, plain, run, tmp_path):
    f = tmp_path / "spec.txt"
    f.write_text("data", encoding="utf-8")
    a = mk(run, plain, "x")
    task = js(run, plain, "attach", a, str(f))
    assert task["links"] == [f"attachments/{a}/spec.txt"]
    assert (home / "attachments" / a / "spec.txt").read_text(encoding="utf-8") == "data"
    assert f.exists()


def test_attach_subtask_uses_root_folder(home, plain, run, tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("d", encoding="utf-8")
    p = mk(run, plain, "p")
    c = mk(run, plain, "c", "--parent", p)
    assert js(run, plain, "attach", c, str(f))["links"] == [f"attachments/{p}/s.txt"]
    assert (home / "attachments" / p / "s.txt").exists()
    assert not (home / "attachments" / c).exists()


def test_attach_errors_copy_nothing(home, plain, run, tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("g", encoding="utf-8")
    other = tmp_path / "sub"
    other.mkdir()
    dup = other / "good.txt"
    dup.write_text("h", encoding="utf-8")
    a = mk(run, plain, "x")
    assert "not a file" in fail(run, plain, "attach", a, str(good), str(tmp_path / "nope"))
    assert "duplicate" in fail(run, plain, "attach", a, str(good), str(dup))
    assert not (home / "attachments" / a / "good.txt").exists()
    js(run, plain, "attach", a, str(good))
    assert "already exists" in fail(run, plain, "attach", a, str(dup))
    extra = tmp_path / "extra.txt"
    extra.write_text("e", encoding="utf-8")
    fail(run, plain, "attach", a, str(extra), str(dup))
    assert not (home / "attachments" / a / "extra.txt").exists()
    assert (home / "attachments" / a / "good.txt").read_text(encoding="utf-8") == "g"


def test_attach_db_failure_removes_copies(home, plain, run, tmp_path, monkeypatch):
    from docket import tasks

    f = tmp_path / "f.txt"
    f.write_text("d", encoding="utf-8")
    a = mk(run, plain, "x")
    real_get = tasks.get

    def boom(conn, task_id):
        if (home / "attachments" / a / "f.txt").exists():
            raise db.DocketError("db failed")
        return real_get(conn, task_id)

    monkeypatch.setattr(tasks, "get", boom)
    assert run("attach", a, str(f), cwd=plain).exit_code == 1
    assert not (home / "attachments" / a / "f.txt").exists()
    monkeypatch.setattr(tasks, "get", real_get)
    assert js(run, plain, "show", a)["links"] == []


# --- edit ---

FAKE_EDITOR = textwrap.dedent(
    """
    import sys
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text + "\\nedited line\\nsecond \\\\n literal\\n")
    """
)


@pytest.fixture
def fake_editor(tmp_path, monkeypatch):
    folder = tmp_path / "dir with space"
    folder.mkdir()
    script = folder / "fake editor.py"
    script.write_text(FAKE_EDITOR, encoding="utf-8")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{script}"')
    return script


def test_edit_saves_changes(home, plain, run, fake_editor):
    a = mk(run, plain, "x")
    task = js(run, plain, "edit", a)
    assert task["body"] == "\nedited line\nsecond \\n literal\n"
    assert "repos: (none)" in ok(run, plain, "edit", a)


def test_edit_unchanged_does_not_write(home, plain, run, tmp_path, monkeypatch):
    script = tmp_path / "noop.py"
    script.write_text("pass\n", encoding="utf-8")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{script}"')
    a = mk(run, plain, "x")
    backups = sorted((home / "backups").iterdir())
    assert js(run, plain, "edit", a)["body"] == ""
    assert sorted((home / "backups").iterdir()) == backups


def test_edit_visual_wins_and_missing_editor(home, plain, run, fake_editor, monkeypatch):
    a = mk(run, plain, "x")
    monkeypatch.setenv("VISUAL", "definitely-not-an-editor-xyz")
    assert "editor not found" in fail(run, plain, "edit", a)
    monkeypatch.delenv("VISUAL")
    monkeypatch.delenv("EDITOR")
    assert "VISUAL" in fail(run, plain, "edit", a)


def test_edit_failing_editor_keeps_body(home, plain, run, tmp_path, monkeypatch):
    script = tmp_path / "bad.py"
    script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{script}"')
    a = mk(run, plain, "x")
    assert "status 3" in fail(run, plain, "edit", a)


def test_edit_detects_concurrent_body_change(home, plain, run, tmp_path, monkeypatch):
    a = mk(run, plain, "x")
    script = tmp_path / "racy.py"
    script.write_text(
        "import subprocess, sys\n"
        f"subprocess.run([{sys.executable!r}, '-m', 'docket', 'note', {a!r}, 'sneaky'],"
        " check=True)\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write('mine')\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{script}"')
    err = fail(run, plain, "edit", a)
    assert "changed during the edit" in err
    saved = err.strip().rsplit("Your edit is in ", 1)[1]
    try:
        with open(saved, encoding="utf-8") as f:
            assert f.read() == "mine"
    finally:
        os.unlink(saved)
    assert "sneaky" in js(run, plain, "show", a)["body"]


def test_edit_removes_temp_file(home, plain, run, fake_editor, monkeypatch):
    import tempfile

    seen = []
    real = tempfile.mkstemp

    def spy(*a, **k):
        fd, name = real(*a, **k)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    a = mk(run, plain, "x")
    run("edit", a, cwd=plain)
    assert seen and all(not os.path.exists(n) for n in seen)


# --- concurrency ---


def test_two_processes_start_the_same_task_once(home, plain, run):
    a = mk(run, plain, "only task")
    env = {**os.environ, "DOCKET_HOME": str(home)}
    cmd = [sys.executable, "-m", "docket", "next", "--start", "--json"]
    procs = [
        subprocess.Popen(
            cmd,
            cwd=plain,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    outs = [p.communicate() for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outs
    results = [json.loads(o) for o, _ in outs]
    got = [r for r in results if r is not None]
    assert len(got) == 1 and got[0]["id"] == a and got[0]["status"] == "doing"


def test_attach_names_collide_case_insensitively(home, plain, run, tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()
    upper = tmp_path / "d1" / "A.txt"
    lower = tmp_path / "d2" / "a.txt"
    upper.write_text("1", encoding="utf-8")
    lower.write_text("2", encoding="utf-8")
    a = mk(run, plain, "x")
    assert "duplicate" in fail(run, plain, "attach", a, str(upper), str(lower))
    assert not (home / "attachments" / a).exists()
    js(run, plain, "attach", a, str(upper))
    assert "already exists" in fail(run, plain, "attach", a, str(lower))
    assert (home / "attachments" / a / "A.txt").read_text(encoding="utf-8") == "1"


def test_dep_rejects_archived_source(home, plain, run):
    a, b = mk(run, plain, "a"), mk(run, plain, "b")
    run("complete", a, cwd=plain)
    assert "archived" in fail(run, plain, "dep", a, b)
    assert js(run, plain, "show", a)["depends_on"] == []


def test_empty_ls_and_search_print_nothing(home, plain, run):
    assert run("ls", cwd=plain).stdout == ""
    assert run("search", "nothing", cwd=plain).stdout == ""
    assert js(run, plain, "ls") == []


def test_many_processes_add_at_once(home, plain):
    env = {**os.environ, "DOCKET_HOME": str(home)}
    count = 6
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "docket", "add", f"task {i}", "--no-repo"],
            cwd=plain,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        for i in range(count)
    ]
    outs = [p.communicate() for p in procs]
    assert [p.returncode for p in procs] == [0] * count, outs
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == count
    finally:
        conn.close()
    assert len(list((home / "backups").glob("docket-*.db"))) == 1
