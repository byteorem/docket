import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def editor(tmp_path: Path) -> tuple[str, Path]:
    """A real editor command, as a config line, that records its arguments to a file."""
    folder = tmp_path / "my editor"
    folder.mkdir()
    log = folder / "argv.json"
    script = folder / "record.py"
    script.write_text(
        "import json, sys\n"
        f"open({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "sys.exit(int(sys.argv[2]) if sys.argv[1] == '--exit' else 0)\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"', log


def configure(store: Path, line: str) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / "config.yaml").write_text(f"editor: {json.dumps(line)}\n", encoding="utf-8")


def opened(log: Path) -> list[str]:
    return json.loads(log.read_text(encoding="utf-8"))


def test_open_without_ids_opens_the_store(store, ok, mk, editor):
    line, log = editor
    mk("Task")
    configure(store, line + " --new")
    data = ok("open")
    assert opened(log) == ["--new", str(store)]
    assert data["paths"] == [str(store)]
    assert data["editor"][0] == sys.executable


def test_open_ids_opens_each_file_once(store, ok, mk, editor):
    line, log = editor
    first, second = mk("First"), mk("Second")
    configure(store, line)
    ok("open", first, second[:3], first)
    tasks = store / "tasks"
    assert opened(log) == [str(tasks / f"{first}.md"), str(tasks / f"{second}.md")]


def test_open_reaches_an_invalid_task_file(store, ok, mk, editor):
    line, log = editor
    task_id = mk("Broken")
    path = store / "tasks" / f"{task_id}.md"
    path.write_text("no frontmatter\n", encoding="utf-8")
    configure(store, line)
    ok("open", task_id)
    assert opened(log) == [str(path)]


def test_config_editor_beats_visual_beats_editor(store, ok, mk, editor, monkeypatch):
    line, log = editor
    mk("Task")
    monkeypatch.setenv("EDITOR", "docket-missing-editor")
    monkeypatch.setenv("VISUAL", line + " --from-visual")
    ok("open")
    assert opened(log)[0] == "--from-visual"
    configure(store, line + " --from-config")
    ok("open")
    assert opened(log)[0] == "--from-config"


def test_open_errors(store, fail, mk, editor):
    line, _ = editor
    task_id = mk("Task")
    configure(store, line)
    assert "no task 'zzzz'" in fail("open", "zzzz")
    configure(store, line + " --exit 3")
    assert "editor exited with 3" in fail("open", task_id)
    configure(store, "docket-missing-editor --wait")
    assert "editor not found: docket-missing-editor --wait" in fail("open")
    (store / "config.yaml").unlink()
    assert "no editor set" in fail("open")


def test_open_before_any_task(store, fail, monkeypatch, editor):
    monkeypatch.setenv("VISUAL", editor[0])
    assert "does not exist yet" in fail("open")
    assert not store.exists()
