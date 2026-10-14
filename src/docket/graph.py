"""Derived state over a set of tasks: states, deps, subtasks, and cycles."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docket.tasks import Task


def parent_id(task_id: str) -> str | None:
    return task_id.rpartition(".")[0] or None


def id_key(task_id: str) -> tuple[str | int, ...]:
    """Sort key that orders a3f8.2 before a3f8.10."""
    root, *rest = task_id.split(".")
    return (root, *(int(n) if n.isdigit() else -1 for n in rest))


class Graph:
    def __init__(self, tasks: Iterable[Task], closed_statuses: Iterable[str]) -> None:
        self.tasks = {t.id: t for t in tasks}
        self.closed = frozenset(closed_statuses)
        self._children: dict[str, list[str]] = defaultdict(list)
        for task_id in sorted(self.tasks, key=id_key):
            parent = parent_id(task_id)
            if parent is not None:
                self._children[parent].append(task_id)

    def is_open(self, task_id: str) -> bool:
        """Open, or missing: a missing dep still blocks."""
        task = self.tasks.get(task_id)
        return task is None or task.status not in self.closed

    def state(self, task: Task) -> str:
        if task.status in self.closed:
            return "closed"
        return "blocked" if self.blockers(task) else "ready"

    def blockers(self, task: Task) -> list[str]:
        """Deps that are open or missing."""
        return [d for d in task.deps if self.is_open(d)]

    def blocking(self, task: Task) -> list[str]:
        """Open tasks waiting on TASK."""
        if task.status in self.closed:
            return []
        return sorted(
            (t.id for t in self.tasks.values() if task.id in t.deps and self.is_open(t.id)),
            key=id_key,
        )

    def children(self, task_id: str) -> list[str]:
        return list(self._children.get(task_id, []))

    def open_descendants(self, task_id: str) -> list[str]:
        prefix = task_id + "."
        found = [i for i in self.tasks if i.startswith(prefix) and self.is_open(i)]
        return sorted(found, key=id_key)

    def dep_path(self, start: str, goal: str) -> list[str] | None:
        """Shortest dep chain from START to GOAL, both included, or None."""
        came_from: dict[str, str | None] = {start: None}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if current == goal:
                path: list[str] = []
                node: str | None = current
                while node is not None:
                    path.append(node)
                    node = came_from[node]
                return path[::-1]
            task = self.tasks.get(current)
            for dep in task.deps if task else []:
                if dep not in came_from:
                    came_from[dep] = current
                    queue.append(dep)
        return None

    def cycles(self) -> list[list[str]]:
        """One dep cycle per back edge, each as [a, b, ..., a], deduplicated."""
        found: dict[tuple[str, ...], list[str]] = {}
        done: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def visit(task_id: str) -> None:
            stack.append(task_id)
            on_stack.add(task_id)
            for dep in self.tasks[task_id].deps:
                if dep in on_stack:
                    loop = stack[stack.index(dep) :]
                    turn = loop.index(min(loop))
                    canonical = loop[turn:] + loop[:turn]
                    found.setdefault(tuple(canonical), [*canonical, canonical[0]])
                elif dep in self.tasks and dep not in done:
                    visit(dep)
            stack.pop()
            on_stack.discard(task_id)
            done.add(task_id)

        for task_id in sorted(self.tasks, key=id_key):
            if task_id not in done:
                visit(task_id)
        return list(found.values())
