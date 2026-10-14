import json
from pathlib import Path

import pytest

from docket import install

HANDLER = {"type": "command", "command": "docket", "args": ["index"]}
RULES = ["Bash(docket *)", "PowerShell(docket *)"]


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "user"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    return path


@pytest.fixture
def settings(user_home: Path) -> Path:
    return user_home / ".claude" / "settings.json"


def write_settings(path: Path, data) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data if isinstance(data, bytes) else json.dumps(data, indent=2).encode()
    path.write_bytes(raw)
    return raw


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- skill ---


def test_skill_default_path(user_home, run):
    result = run("install", "skill")
    target = user_home / ".claude" / "skills" / "docket" / "SKILL.md"
    assert result.exit_code == 0
    assert result.stdout.strip() == f"wrote {target}"
    assert target.read_text(encoding="utf-8").startswith("---\nname: docket\n")


def test_skill_dir_json(tmp_path, run):
    result = run("install", "skill", "--dir", str(tmp_path / "skills"), "--json")
    target = tmp_path / "skills" / "docket" / "SKILL.md"
    assert json.loads(result.stdout) == {"path": str(target)}
    assert target.is_file()


def test_skill_overwrites_and_spares_siblings(tmp_path):
    skills = tmp_path / "skills"
    (skills / "docket").mkdir(parents=True)
    (skills / "docket" / "SKILL.md").write_text("old")
    (skills / "docket" / "extra.txt").write_text("keep")
    (skills / "other").mkdir()
    (skills / "other" / "SKILL.md").write_text("other")
    install.install_skill(skills)
    assert (skills / "docket" / "SKILL.md").read_text(encoding="utf-8") != "old"
    assert (skills / "docket" / "extra.txt").read_text() == "keep"
    assert (skills / "other" / "SKILL.md").read_text() == "other"


# --- hook ---


def test_hook_creates_missing_file(settings, run):
    assert not settings.parent.exists()
    result = run("install", "hook")
    assert result.exit_code == 0
    assert load(settings) == {
        "hooks": {"SessionStart": [{"hooks": [HANDLER]}]},
        "permissions": {"allow": RULES},
    }
    raw = settings.read_text(encoding="utf-8")
    assert raw.endswith("}\n") and '\n  "hooks"' in raw
    assert not settings.with_name("settings.json.bak").exists()
    assert str(settings) in result.stdout


def test_hook_preserves_everything_else(settings):
    original = {
        "model": "opus",
        "permissions": {"allow": ["Bash(git *)"], "deny": ["Bash(rm *)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}],
            "SessionStart": [
                {"matcher": "startup", "hooks": [{"type": "command", "command": "y"}]}
            ],
        },
        "env": {"A": "1"},
    }
    raw = write_settings(settings, original)
    result = install.install_hook()
    data = load(settings)
    assert list(data) == ["model", "permissions", "hooks", "env"]
    assert data["model"] == "opus" and data["env"] == {"A": "1"}
    assert data["permissions"]["deny"] == ["Bash(rm *)"]
    assert data["permissions"]["allow"] == ["Bash(git *)", *RULES]
    assert data["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    assert data["hooks"]["SessionStart"] == [
        *original["hooks"]["SessionStart"],
        {"hooks": [HANDLER]},
    ]
    assert settings.with_name("settings.json.bak").read_bytes() == raw
    assert result["backup"] == str(settings.with_name("settings.json.bak"))
    assert len(result["added"]) == 3


@pytest.mark.parametrize(
    "existing",
    [
        {"type": "command", "command": "docket", "args": ["index"]},
        {"type": "command", "command": "docket index"},
    ],
    ids=["exec-form", "shell-form"],
)
def test_hook_existing_handler_adds_only_rules(settings, existing):
    write_settings(settings, {"hooks": {"SessionStart": [{"hooks": [existing]}]}})
    result = install.install_hook()
    data = load(settings)
    assert data["hooks"]["SessionStart"] == [{"hooks": [existing]}]
    assert data["permissions"]["allow"] == RULES
    assert result["added"] == [f"permission: {r}" for r in RULES]


def test_hook_other_docket_args_not_counted(settings):
    other = {"type": "command", "command": "docket", "args": ["ls"]}
    write_settings(settings, {"hooks": {"SessionStart": [{"hooks": [other]}]}})
    install.install_hook()
    assert load(settings)["hooks"]["SessionStart"] == [{"hooks": [other]}, {"hooks": [HANDLER]}]


def test_hook_existing_rule_adds_only_missing(settings):
    write_settings(
        settings,
        {
            "permissions": {"allow": ["Bash(docket *)"]},
            "hooks": {"SessionStart": [{"hooks": [HANDLER]}]},
        },
    )
    result = install.install_hook()
    assert load(settings)["permissions"]["allow"] == RULES
    assert result["added"] == ["permission: PowerShell(docket *)"]


def test_hook_fully_installed_changes_nothing(settings, run):
    raw = write_settings(
        settings,
        {"permissions": {"allow": RULES}, "hooks": {"SessionStart": [{"hooks": [HANDLER]}]}},
    )
    before = settings.stat().st_mtime_ns
    result = run("install", "hook")
    assert result.exit_code == 0
    assert "already installed; nothing changed" in result.stdout
    assert settings.read_bytes() == raw
    assert settings.stat().st_mtime_ns == before
    assert not settings.with_name("settings.json.bak").exists()


def test_hook_json_output(settings, run):
    result = run("install", "hook", "--json")
    assert json.loads(result.stdout) == {
        "path": str(settings),
        "added": ["SessionStart hook: docket index", *[f"permission: {r}" for r in RULES]],
        "backup": None,
    }


def test_hook_invalid_json(settings, run):
    raw = write_settings(settings, b'{"hooks": ')
    result = run("install", "hook")
    assert result.exit_code == 1
    assert result.stderr.count("\n") == 1 and "not valid JSON" in result.stderr
    assert settings.read_bytes() == raw
    assert not settings.with_name("settings.json.bak").exists()


@pytest.mark.parametrize("value", [[], "text", 3, None])
def test_hook_non_object_top_level(settings, run, value):
    raw = write_settings(settings, json.dumps(value).encode())
    result = run("install", "hook")
    assert result.exit_code == 1
    assert "JSON object" in result.stderr
    assert settings.read_bytes() == raw


@pytest.mark.parametrize(
    "data, key",
    [
        ({"hooks": []}, "hooks"),
        ({"hooks": {"SessionStart": {}}}, "hooks.SessionStart"),
        ({"permissions": []}, "permissions"),
        ({"permissions": {"allow": "Bash"}}, "permissions.allow"),
    ],
)
def test_hook_wrong_nested_types(settings, run, data, key):
    raw = write_settings(settings, data)
    result = run("install", "hook")
    assert result.exit_code == 1
    assert f"'{key}'" in result.stderr and "Traceback" not in result.stderr
    assert settings.read_bytes() == raw


def test_path_warning(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert "needs `docket` on PATH" in install.path_warning()
    monkeypatch.setattr(install.shutil, "which", lambda name: "C:/bin/docket.exe")
    assert install.path_warning() is None


def test_hook_accepts_utf8_bom(settings):
    raw = write_settings(settings, b"\xef\xbb\xbf" + json.dumps({"model": "opus"}).encode())
    install.install_hook()
    data = load(settings)
    assert data["model"] == "opus"
    assert data["permissions"]["allow"] == RULES
    assert not settings.read_bytes().startswith(b"\xef\xbb\xbf")
    assert settings.with_name("settings.json.bak").read_bytes() == raw


def test_hook_write_failure_is_one_line(settings, run, monkeypatch):
    raw = write_settings(settings, {"model": "opus"})

    def deny(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(install.os, "replace", deny)
    result = run("install", "hook")
    assert result.exit_code == 1
    assert result.stderr.count("\n") == 1 and "could not write" in result.stderr
    assert settings.read_bytes() == raw
    assert not list(settings.parent.glob("*.tmp"))
