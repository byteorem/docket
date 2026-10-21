"""check: report what the other commands skip or cannot see."""

from __future__ import annotations

from typing import Any

from docket.graph import id_key
from docket.tasks import Snapshot


def check(s: Snapshot) -> dict[str, Any]:
    found: list[tuple[str, str, str]] = []
    for stem, error in s.invalid.items():
        found.append((stem, error.kind, str(error)))
    for task in s.graph.tasks.values():
        raw = task.path.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            line = raw[: e.start].count(b"\n") + 1
            found.append((task.id, "encoding", f"{task.id} is not valid UTF-8 on line {line}"))
        if task.parent is not None and task.parent not in s.paths:
            found.append((task.id, "orphan", f"{task.id} has no parent {task.parent}"))
        for dep in task.deps:
            if dep not in s.paths:
                found.append((task.id, "missing-dep", f"{task.id} depends on missing task {dep}"))
    for loop in s.graph.cycles():
        found.append((loop[0], "cycle", f"dependency cycle: {' -> '.join(loop)}"))

    found.sort(key=lambda p: (id_key(p[0]), p[1], p[2]))
    problems = [
        {"path": str(s.paths[stem]), "kind": kind, "message": message}
        for stem, kind, message in found
    ]
    return {"problems": problems}
