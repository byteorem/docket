"""Install the bundled agent skill and the Claude Code SessionStart hook."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from importlib import resources
from pathlib import Path

from docket.store import DocketError

HANDLER = {"type": "command", "command": "docket", "args": ["index"]}
PERMISSION_RULES = ["Bash(docket *)", "PowerShell(docket *)"]


def install_skill(dir: Path | None) -> dict:
    base = dir if dir is not None else Path.home() / ".claude" / "skills"
    target = base / "docket" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((resources.files("docket") / "SKILL.md").read_bytes())
    return {"path": str(target)}


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _is_docket_handler(h: object) -> bool:
    if not isinstance(h, dict) or h.get("type", "command") != "command":
        return False
    command = h.get("command")
    if command == "docket":
        return h.get("args") == HANDLER["args"]
    return isinstance(command, str) and "args" not in h and command.strip() == "docket index"


def _has_handler(entries: list) -> bool:
    return any(
        isinstance(e, dict)
        and isinstance(e.get("hooks"), list)
        and any(_is_docket_handler(h) for h in e["hooks"])
        for e in entries
    )


def _child(parent: dict, key: str, kind: type, label: str) -> object:
    """Return parent[key], creating it empty when absent; error when it has the wrong type."""
    if key not in parent:
        parent[key] = kind()
    elif not isinstance(parent[key], kind):
        want = "an object" if kind is dict else "an array"
        raise DocketError(f"settings.json: '{label}' must be {want}")
    return parent[key]


def install_hook() -> dict:
    path = settings_path()
    original: bytes | None = None
    settings: object = {}
    if path.exists():
        original = path.read_bytes()
        try:
            settings = json.loads(original.decode("utf-8-sig"))
        except ValueError as e:
            raise DocketError(f"{path} is not valid JSON ({e}); fix it and retry") from None
        if not isinstance(settings, dict):
            raise DocketError(f"{path}: top-level value must be a JSON object")

    assert isinstance(settings, dict)
    added: list[str] = []

    # Validate every nested type before mutating, so an error leaves nothing half-applied.
    hooks = _child(settings, "hooks", dict, "hooks")
    session_start = _child(hooks, "SessionStart", list, "hooks.SessionStart")
    permissions = _child(settings, "permissions", dict, "permissions")
    allow = _child(permissions, "allow", list, "permissions.allow")

    if not _has_handler(session_start):
        session_start.append({"hooks": [dict(HANDLER, args=list(HANDLER["args"]))]})
        added.append("SessionStart hook: docket index")
    for rule in PERMISSION_RULES:
        if rule not in allow:
            allow.append(rule)
            added.append(f"permission: {rule}")

    backup: Path | None = None
    if added:
        tmp: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if original is not None:
                backup = path.with_name(path.name + ".bak")
                shutil.copy2(path, backup)
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
                f.write("\n")
            if original is not None:
                shutil.copymode(path, tmp)
            os.replace(tmp, path)
        except OSError as e:
            raise DocketError(f"could not write {path}: {e.strerror or e}") from e
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    return {"path": str(path), "added": added, "backup": str(backup) if backup else None}


def path_warning() -> str | None:
    """Warning when the hook's `docket` would not resolve to an executable."""
    found = shutil.which("docket")
    if found is None or (sys.platform == "win32" and not found.lower().endswith(".exe")):
        return (
            "warning: the hook runs `docket` directly, so it needs `docket` on PATH as an "
            "executable (install it with `uv tool install`)"
        )
    return None
