import json

import pytest
from helpers import js, mk, ok


@pytest.fixture
def plain(home, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    return d


def seed(run, plain):
    a = mk(run, plain, "Café ✓", "--tag", "x", "--tag", "y")
    run("note", a, "line one", cwd=plain)
    run("note", a, "ligne deux éè", cwd=plain)
    b = mk(run, plain, "Done one", "--dep", a)
    run("complete", b, cwd=plain)
    return a, b


def test_export_jsonl_stdout_round_trips(home, plain, run):
    a, b = seed(run, plain)
    rows = [json.loads(line) for line in ok(run, plain, "export").splitlines()]
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {a, b}
    assert by_id[a] == js(run, plain, "show", a)
    assert by_id[a]["body"].count("\n") == 1 and "éè" in by_id[a]["body"]
    assert by_id[b]["outcome"] == "done"
    assert "✓" in ok(run, plain, "export")  # not ASCII-escaped
    assert len(js(run, plain, "export")) == 2


def test_export_jsonl_out(home, plain, run, tmp_path):
    seed(run, plain)
    out = tmp_path / "new" / "dir"
    assert ok(run, plain, "export", "--out", str(out)).strip() == (
        f"exported 2 tasks to {out / 'docket.jsonl'}"
    )
    raw = (out / "docket.jsonl").read_bytes()
    assert b"\r" not in raw and len(raw.decode("utf-8").splitlines()) == 2
    data = js(run, plain, "export", "--out", str(out))
    assert data == {"format": "jsonl", "path": str(out / "docket.jsonl"), "count": 2}


def test_export_md(home, plain, run, tmp_path):
    a, b = seed(run, plain)
    out = tmp_path / "md"
    text_out = ok(run, plain, "export", "--format", "md", "--out", str(out))
    assert text_out.startswith("exported 2 tasks to")
    raw = (out / f"{a}.md").read_bytes()
    assert b"\r" not in raw
    head, _, body = raw.decode("utf-8").partition("\n---\n\n")
    assert head.startswith(f"---\nid: {a}\nparent: \ntitle: Café ✓\nstatus: todo\n")
    assert "tags: x, y\n" in head and "\nbody" not in head
    assert body.endswith("ligne deux éè") and "line one" in body
    done = (out / f"{b}.md").read_text(encoding="utf-8")
    assert f"depends_on: {a}\n" in done and "outcome: done\n" in done
    (out / f"{a}.md").write_text("stale", encoding="utf-8")
    data = js(run, plain, "export", "--format", "md", "--out", str(out))
    assert data["count"] == 2 and data["format"] == "md"
    assert (out / f"{a}.md").read_text(encoding="utf-8").startswith("---")


def test_export_usage_errors(home, plain, run):
    assert run("export", "--format", "md", cwd=plain).exit_code == 2
    assert run("export", "--format", "xml", cwd=plain).exit_code == 2


def test_export_empty_store(home, plain, run):
    assert ok(run, plain, "export") == ""
    assert js(run, plain, "export") == []
