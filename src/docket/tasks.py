"""Task files: frontmatter read and write, IDs, field rules, and the commands built on them."""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import string
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from docket.graph import Graph, id_key, parent_id
from docket.store import (
    NO_STORE,
    Config,
    DocketError,
    Location,
    atomic_write,
    discover,
    load_config,
    lock,
    user_home,
)

CORE = ("id", "title", "status", "created", "closed_at", "deps")
REQUIRED = ("id", "title", "status", "created")
COMPUTED = ("path", "state", "parent", "body", "blockers", "blocking", "subtasks", "open_subtasks")
ID_RE = re.compile(r"[a-z0-9]{4}(\.[1-9][0-9]*)*")
KEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
ID_CHARS = string.ascii_lowercase + string.digits
BOM = b"\xef\xbb\xbf"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- frontmatter ---

_TIMESTAMP = "tag:yaml.org,2002:timestamp"


def _without_timestamps(resolvers: dict[Any, list[Any]]) -> dict[Any, list[Any]]:
    return {k: [(tag, rx) for tag, rx in v if tag != _TIMESTAMP] for k, v in resolvers.items()}


class _Loader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # type: ignore[misc]
    """Safe loader that keeps timestamps as the strings they were written as."""


_Loader.yaml_implicit_resolvers = _without_timestamps(yaml.SafeLoader.yaml_implicit_resolvers)


class _Quoted(str):
    """Dumped double-quoted, so YAML never reads an ID such as 1234 as a number."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.yaml_implicit_resolvers = _without_timestamps(yaml.SafeDumper.yaml_implicit_resolvers)
_Dumper.add_representer(
    _Quoted, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", str(s), style='"')
)
_Dumper.add_representer(
    list, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True)
)


class InvalidTask(Exception):
    def __init__(self, message: str, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def _frontmatter(lines: Iterator[bytes]) -> dict[str, Any]:
    """Consume LINES through the closing fence and return the parsed frontmatter."""
    first = next(lines, b"")
    if first.removeprefix(BOM).rstrip(b"\r\n") != b"---":
        raise InvalidTask("file does not start with ---")
    block = []
    for line in lines:
        if line.rstrip(b"\r\n") == b"---":
            break
        block.append(line)
    else:
        raise InvalidTask("frontmatter has no closing ---")
    try:
        meta = yaml.load(b"".join(block).decode("utf-8"), Loader=_Loader)
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise InvalidTask(f"invalid frontmatter: {' '.join(str(e).split())}") from None
    if not isinstance(meta, dict):
        raise InvalidTask("frontmatter is not a mapping")
    return meta


def read_meta(path: Path) -> dict[str, Any]:
    """Parse the frontmatter only, stopping at the closing fence."""
    with path.open("rb") as f:
        return _frontmatter(iter(f))


def read_file(path: Path) -> tuple[dict[str, Any], bytes]:
    """Frontmatter and the body's exact bytes."""
    return split(path.read_bytes())


def split(raw: bytes) -> tuple[dict[str, Any], bytes]:
    lines = iter(raw.splitlines(keepends=True))
    meta = _frontmatter(lines)
    return meta, b"".join(lines)


def validate(meta: dict[str, Any], stem: str) -> None:
    for key in REQUIRED:
        if key not in meta:
            raise InvalidTask(f"missing {key}")
    task_id = meta["id"]
    if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id):
        raise InvalidTask(f"id {task_id!r} is not a quoted task ID")
    if task_id != stem:
        raise InvalidTask(f"id '{task_id}' does not match filename '{stem}.md'", "id-mismatch")
    for key in ("title", "status"):
        if not _is_line(meta[key]):
            raise InvalidTask(f"{key} must be a one-line string")
    for key in ("created", "closed_at"):
        if key in meta and not isinstance(meta[key], str):
            raise InvalidTask(f"{key} must be a timestamp string")
    deps = meta.get("deps", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise InvalidTask("deps must be a list of quoted task IDs")


def dump(meta: dict[str, Any]) -> bytes:
    """Frontmatter with fences: core fields in table order, then user fields as they were."""
    ordered = {k: meta[k] for k in CORE if not _is_empty(meta.get(k))}
    ordered |= {k: v for k, v in meta.items() if k not in CORE and not _is_empty(v)}
    ordered["id"] = _Quoted(ordered["id"])
    if "deps" in ordered:
        ordered["deps"] = [_Quoted(d) for d in ordered["deps"]]
    text = yaml.dump(
        ordered,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=2**31,
    )
    return b"---\n" + text.encode("utf-8") + b"---\n"


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_line(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.splitlines() == [value]


# --- the store as read ---


@dataclass
class Task:
    path: Path
    meta: dict[str, Any]

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def title(self) -> str:
        return self.meta["title"]

    @property
    def status(self) -> str:
        return self.meta["status"]

    @property
    def deps(self) -> list[str]:
        return self.meta.get("deps", [])

    @property
    def parent(self) -> str | None:
        return parent_id(self.id)


@dataclass
class Snapshot:
    location: Location
    config: Config
    graph: Graph
    # Every task file by filename stem, valid or not, so IDs stay unique and resolvable.
    paths: dict[str, Path]
    invalid: dict[str, InvalidTask] = field(default_factory=dict)

    @property
    def folder(self) -> Path:
        return self.location.require() / "tasks"

    def resolve(self, ref: str) -> str:
        if self.location.store is None:
            raise DocketError(f"no task '{ref}': {NO_STORE}")
        return _match(ref, self.paths, f"no task '{ref}'")

    def get(self, ref: str) -> Task:
        task_id = self.resolve(ref)
        if task_id in self.invalid:
            raise DocketError(
                f"{self.paths[task_id]}: {self.invalid[task_id]}; fix it by hand (see docket check)"
            )
        return self.graph.tasks[task_id]

    def is_closed(self, task: Task) -> bool:
        return task.status in self.config.closed_statuses

    def object(self, task: Task, **extra: Any) -> dict[str, Any]:
        """The JSON task object: frontmatter as written, plus path, state, and parent."""
        return {
            **{k: v for k, v in task.meta.items() if not _is_empty(v)},
            "deps": task.deps,
            "path": str(task.path),
            "state": self.graph.state(task),
            "parent": task.parent,
            **extra,
        }


def _match(ref: str, ids: Iterable[str], missing: str) -> str:
    """Exact ID, else the one ID starting with REF."""
    ids = list(ids)
    if ref in ids:
        return ref
    found = sorted((i for i in ids if i.startswith(ref)), key=id_key)
    if not found:
        raise DocketError(missing)
    if len(found) > 1:
        raise DocketError(f"ambiguous ID '{ref}': {', '.join(found)}")
    return found[0]


def snapshot(location: Location | None = None) -> Snapshot:
    location = location or discover()
    config = load_config(location.store)
    loaded: list[Task] = []
    paths: dict[str, Path] = {}
    invalid: dict[str, InvalidTask] = {}
    folder = location.store / "tasks" if location.store else None
    if folder is not None and folder.is_dir():
        for path in folder.glob("*.md"):
            paths[path.stem] = path
            try:
                meta = read_meta(path)
                validate(meta, path.stem)
            except InvalidTask as e:
                invalid[path.stem] = e
            else:
                loaded.append(Task(path, meta))
    graph = Graph(loaded, config.closed_statuses)
    return Snapshot(location, config, graph, paths, invalid)


@contextmanager
def writing() -> Iterator[Snapshot]:
    """A snapshot taken and used under the store lock."""
    location = discover()
    with lock(location.require()):
        yield snapshot(location)


def _save(task: Task) -> None:
    _, body = read_file(task.path)
    atomic_write(task.path, dump(task.meta) + body)


# --- field rules ---


def _check_line(key: str, value: str) -> None:
    if not _is_line(value):
        raise DocketError(f"{key} must be one non-empty line")


def _check_user_key(key: str) -> None:
    if key == "deps":
        raise DocketError("deps is changed with docket dep and docket undep")
    if key in ("title", "status"):
        raise DocketError(f"{key} is required; change it with docket set")
    if key in CORE:
        raise DocketError(f"{key} is set by docket")
    if key in COMPUTED:
        raise DocketError(f"'{key}' is reserved for computed output")
    if not KEY_RE.fullmatch(key):
        raise DocketError(f"invalid field name '{key}'")


def _set_status(s: Snapshot, task: Task, status: str, force: bool) -> None:
    """Change status, enforcing the subtask rules on entering or leaving a closed status."""
    _check_line("status", status)
    was_closed = s.is_closed(task)
    closing = status in s.config.closed_statuses
    if closing and not was_closed:
        open_subtasks = s.graph.open_descendants(task.id)
        if open_subtasks and not force:
            raise DocketError(
                f"{task.id} has open subtasks: {', '.join(open_subtasks)}; close them first "
                f"or use docket close --force"
            )
        task.meta["closed_at"] = now()
    elif was_closed and not closing:
        parent = s.graph.tasks.get(task.parent) if task.parent else None
        if parent is not None and s.is_closed(parent) and not force:
            raise DocketError(
                f"parent {parent.id} is closed; reopen it first or use docket reopen --force"
            )
        task.meta.pop("closed_at", None)
    task.meta["status"] = status


# --- write commands ---


def create(
    title: str,
    *,
    parent: str | None = None,
    deps: Iterable[str] = (),
    fields: Iterable[tuple[str, str]] = (),
    body: str = "",
) -> dict[str, Any]:
    _check_line("title", title)
    fields = list(fields)
    for key, _ in fields:
        _check_user_key(key)
    with writing() as s:
        if parent is not None:
            parent_task = s.get(parent)
            if s.is_closed(parent_task):
                raise DocketError(f"parent {parent_task.id} is closed; reopen it first")
            task_id = _child_id(s, parent_task.id)
        else:
            task_id = _new_id(s)
        dep_ids = list(dict.fromkeys(s.get(d).id for d in deps))
        meta = {
            "id": task_id,
            "title": title,
            "status": s.config.default_status,
            "created": now(),
            "deps": dep_ids,
            **dict(fields),
        }
        path = s.folder / f"{task_id}.md"
        text = body.strip("\n")
        atomic_write(path, dump(meta) + (f"\n{text}\n".encode() if text.strip() else b""))
        task = Task(path, meta)
        s.graph = Graph([*s.graph.tasks.values(), task], s.config.closed_statuses)
        return s.object(task)


def _new_id(s: Snapshot) -> str:
    while True:
        task_id = "".join(secrets.choice(ID_CHARS) for _ in range(4))
        if task_id not in s.paths:
            return task_id


def _child_id(s: Snapshot, parent: str) -> str:
    numbers = [
        int(i.rpartition(".")[2]) for i in s.paths if ID_RE.fullmatch(i) and parent_id(i) == parent
    ]
    return f"{parent}.{max(numbers, default=0) + 1}"


def set_field(ref: str, key: str, value: str) -> dict[str, Any]:
    with writing() as s:
        task = s.get(ref)
        if key == "title":
            _check_line("title", value)
            task.meta["title"] = value
        elif key == "status":
            _set_status(s, task, value, force=False)
        else:
            _check_user_key(key)
            task.meta[key] = value
        _save(task)
        return s.object(task)


def unset_field(ref: str, key: str) -> dict[str, Any]:
    _check_user_key(key)
    with writing() as s:
        task = s.get(ref)
        if key not in task.meta:
            raise DocketError(f"{task.id} has no field '{key}'")
        del task.meta[key]
        _save(task)
        return s.object(task)


def add_value(ref: str, key: str, value: str) -> dict[str, Any]:
    _check_user_key(key)
    with writing() as s:
        task = s.get(ref)
        current = task.meta.get(key, [])
        if not isinstance(current, list):
            raise DocketError(f"{key} is not a list; unset it first")
        if value in current:
            raise DocketError(f"{key} already contains '{value}'")
        task.meta[key] = [*current, value]
        _save(task)
        return s.object(task)


def remove_value(ref: str, key: str, value: str) -> dict[str, Any]:
    _check_user_key(key)
    with writing() as s:
        task = s.get(ref)
        current = task.meta.get(key)
        if not isinstance(current, list):
            raise DocketError(f"{task.id} has no list field '{key}'")
        if value not in current:
            raise DocketError(f"{key} does not contain '{value}'")
        task.meta[key] = [v for v in current if v != value]
        _save(task)
        return s.object(task)


def close(ref: str, *, status: str | None = None, force: bool = False) -> dict[str, Any]:
    with writing() as s:
        task = s.get(ref)
        closed = s.config.closed_statuses
        target = status or closed[0]
        if target not in closed:
            raise DocketError(f"'{target}' is not a closed status; use one of: {', '.join(closed)}")
        if task.status == target:
            raise DocketError(f"{task.id} is already {target}")
        _set_status(s, task, target, force)
        _save(task)
        return s.object(task)


def reopen(ref: str, *, force: bool = False) -> dict[str, Any]:
    with writing() as s:
        task = s.get(ref)
        if not s.is_closed(task):
            raise DocketError(f"{task.id} is not closed")
        _set_status(s, task, s.config.default_status, force)
        _save(task)
        return s.object(task)


def dep(ref: str, dep_ref: str) -> dict[str, Any]:
    with writing() as s:
        task = s.get(ref)
        target = s.get(dep_ref)
        if target.id == task.id:
            raise DocketError("a task cannot depend on itself")
        if target.id in task.deps:
            raise DocketError(f"{task.id} already depends on {target.id}")
        loop = s.graph.dep_path(target.id, task.id)
        if loop:
            raise DocketError(f"dependency cycle: {' -> '.join([task.id, *loop])}")
        task.meta["deps"] = [*task.deps, target.id]
        _save(task)
        return s.object(task)


def undep(ref: str, dep_ref: str) -> dict[str, Any]:
    """Match DEP_REF against the task's own deps, so a dep on a missing task can go too."""
    with writing() as s:
        task = s.get(ref)
        target = _match(dep_ref, task.deps, f"{task.id} does not depend on '{dep_ref}'")
        task.meta["deps"] = [d for d in task.deps if d != target]
        _save(task)
        return s.object(task)


def note(ref: str, text: str) -> dict[str, Any]:
    text = text.rstrip()
    if not text:
        raise DocketError("note text is empty")
    return edit_body(ref, "append", f"**{now()}** {text}", section=NOTES)


# --- body ---

BodyMode = Literal["set", "append", "replace"]
NOTES = "Notes"
HEADING_RE = re.compile(r" {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*")
FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")


def edit_body(
    ref: str, mode: BodyMode, text: str, *, old: str = "", section: str | None = None
) -> dict[str, Any]:
    """Replace, append to, or swap one exact match in the body, or in the section SECTION."""
    text = text.replace("\r\n", "\n")
    if mode == "replace":
        old = old.replace("\r\n", "\n")
        if not old:
            raise DocketError("text to replace is empty")
    else:
        text = text.strip("\n")
        if not text.strip():
            raise DocketError("body text is empty")
    if section is not None:
        section = _section_name(section)
    with writing() as s:
        task = s.get(ref)
        doc, first_line = _read_body(task)
        nl = "\r\n" if "\r\n" in doc else "\n"
        text, old = text.replace("\n", nl), old.replace("\n", nl)
        if section is None:
            doc = _edit_scope(doc, mode, text, old, nl, f"the body of {task.id}")
        else:
            doc = _edit_section(doc, mode, text, old, nl, section, task.id, first_line)
        atomic_write(task.path, dump(task.meta) + doc.encode("utf-8"))
        return s.object(task)


def decode(path: Path, raw: bytes) -> str:
    """RAW as text; a task file that isn't UTF-8 is for the user to fix."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        line = raw[: e.start].count(b"\n") + 1
        raise DocketError(
            f"{path}: not valid UTF-8 on line {line}; fix it by hand (see docket check)"
        ) from None


def _read_body(task: Task) -> tuple[str, int]:
    """The body as text, and the file line it starts on."""
    raw = task.path.read_bytes()
    decode(task.path, raw)
    _, body = split(raw)
    return body.decode("utf-8"), raw[: len(raw) - len(body)].count(b"\n") + 1


def _section_name(name: str) -> str:
    """NAME without the heading marks an agent may copy along with it."""
    name = name.strip().lstrip("#").strip()
    _check_line("section", name)
    return name


def _edit_scope(scope: str, mode: BodyMode, text: str, old: str, nl: str, label: str) -> str:
    """SCOPE, the whole body, after the edit."""
    if mode == "set":
        return nl + text + nl
    if mode == "append":
        if scope and not scope.endswith("\n"):
            scope += nl
        return scope + nl + text + nl
    return _swap(scope, old, text, label)


def _edit_section(
    doc: str,
    mode: BodyMode,
    text: str,
    old: str,
    nl: str,
    name: str,
    task_id: str,
    first_line: int,
) -> str:
    lines = _lines(doc)
    label = f"section '{name}' of {task_id}"
    span = _find_section(lines, name, label, first_line)
    if span is None:
        if mode == "replace":
            raise DocketError(f"no {label}")
        return _edit_scope(doc, "append", f"## {name}{nl}{nl}{text}", "", nl, label)
    start, end = span
    head = "".join(lines[: start + 1])
    if not head.endswith("\n"):
        head += nl
    content, tail = "".join(lines[start + 1 : end]), "".join(lines[end:])
    gap = nl if tail else ""
    if mode == "set":
        content = nl + text + nl + gap
    elif mode == "append":
        kept = content.rstrip() + nl if content.strip() else ""
        content = kept + nl + text + nl + gap
    else:
        content = _swap(content, old, text, label)
    return head + content + tail


def _swap(scope: str, old: str, new: str, label: str) -> str:
    count = scope.count(old)
    if count == 0:
        raise DocketError(f"text to replace not found in {label}")
    if count > 1:
        raise DocketError(
            f"text to replace appears {count} times in {label}; include more surrounding text"
        )
    return scope.replace(old, new, 1)


def _lines(doc: str) -> list[str]:
    return [line for line in re.split(r"(?<=\n)", doc) if line]


def _find_section(
    lines: list[str], name: str, label: str, first_line: int
) -> tuple[int, int] | None:
    """Index of the heading NAME and of the line that ends its section, or None."""
    headings = _headings(lines)
    found = [(i, level) for i, level, title in headings if title.casefold() == name.casefold()]
    if len(found) > 1:
        at = ", ".join(str(first_line + i) for i, _ in found)
        raise DocketError(f"{label} appears {len(found)} times (lines {at}); rename one")
    if not found:
        return None
    start, level = found[0]
    end = next((i for i, lv, _ in headings if i > start and lv <= level), len(lines))
    return start, end


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Line index, level, and text of each ATX heading outside fenced code."""
    found = []
    fence: str | None = None
    for i, line in enumerate(lines):
        line = line.rstrip("\r\n")
        m = FENCE_RE.match(line)
        if fence is not None:
            marker = m.group(1) if m else ""
            if marker[:1] == fence[0] and len(marker) >= len(fence) and not line[m.end() :].strip():
                fence = None
        elif m:
            fence = m.group(1)
        elif heading := HEADING_RE.fullmatch(line):
            found.append((i, len(heading.group(1)), heading.group(2)))
    return found


# --- read commands ---


def show(s: Snapshot, ref: str) -> dict[str, Any]:
    """The task object with body and relations, the file text, and a summary per related ID."""
    task = s.get(ref)
    raw = task.path.read_bytes()
    text = decode(task.path, raw)
    _, body = split(raw)
    blockers = s.graph.blockers(task)
    blocking = s.graph.blocking(task)
    subtasks = s.graph.children(task.id)
    related = [*([task.parent] if task.parent else []), *blockers, *blocking, *subtasks]
    return {
        "task": s.object(
            task,
            body=body.decode("utf-8").replace("\r\n", "\n").lstrip("\n"),
            blockers=blockers,
            blocking=blocking,
            subtasks=subtasks,
        ),
        "file": text.removeprefix("﻿").replace("\r\n", "\n"),
        "related": {i: s.object(s.graph.tasks[i]) if i in s.graph.tasks else None for i in related},
    }


def show_section(s: Snapshot, ref: str, name: str) -> dict[str, Any]:
    """The text under the heading NAME, without the heading."""
    name = _section_name(name)
    task = s.get(ref)
    doc, first_line = _read_body(task)
    lines = _lines(doc.replace("\r\n", "\n"))
    label = f"section '{name}' of {task.id}"
    span = _find_section(lines, name, label, first_line)
    if span is None:
        raise DocketError(f"no {label}")
    start, end = span
    return {"id": task.id, "section": name, "text": "".join(lines[start + 1 : end]).strip("\n")}


def matches(value: Any, wanted: str) -> bool:
    """A string equal to WANTED, or a list containing it."""
    if isinstance(value, list):
        return any(_as_text(v) == wanted for v in value)
    return _as_text(value) == wanted


def _as_text(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int | float):
        return str(value)
    return None


def select(
    s: Snapshot,
    *,
    states: Iterable[str],
    status: str | None = None,
    where: Iterable[tuple[str, str]] = (),
    tree: bool = False,
) -> list[dict[str, Any]]:
    """Task objects in STATES matching every filter, oldest first or as a tree."""
    states = set(states)
    where = list(where)
    found = sorted(
        (
            t
            for t in s.graph.tasks.values()
            if s.graph.state(t) in states
            and (status is None or t.status == status)
            and all(matches(t.meta.get(k), v) for k, v in where)
        ),
        key=lambda t: (t.meta["created"], id_key(t.id)),
    )
    if tree:
        found = _tree_order(found)
    blocked = states == {"blocked"}
    return [s.object(t, blockers=s.graph.blockers(t)) if blocked else s.object(t) for t in found]


def _tree_order(found: list[Task]) -> list[Task]:
    """Each task followed by its listed descendants, under the nearest listed ancestor."""
    ids = {t.id for t in found}
    roots: list[Task] = []
    kids: dict[str, list[Task]] = defaultdict(list)
    for t in found:
        ancestor = t.parent
        while ancestor is not None and ancestor not in ids:
            ancestor = parent_id(ancestor)
        (kids[ancestor] if ancestor else roots).append(t)
    ordered: list[Task] = []

    def walk(t: Task) -> None:
        ordered.append(t)
        for child in sorted(kids[t.id], key=lambda c: id_key(c.id)):
            walk(child)

    for root in roots:
        walk(root)
    return ordered


def index(s: Snapshot, limit: int) -> dict[str, Any] | None:
    """Top-level session summary, or None when the store does not exist."""
    store = s.location.store
    if store is None or not store.is_dir():
        return None
    top = sorted(
        (t for t in s.graph.tasks.values() if t.parent is None and not s.is_closed(t)),
        key=lambda t: (t.meta["created"], t.id),
    )
    default = s.config.default_status
    underway = [t for t in top if t.status != default]
    ready = [t for t in top if t.status == default and s.graph.state(t) == "ready"]
    blocked = sum(1 for t in top if t.status == default and s.graph.state(t) == "blocked")
    shown_underway = underway[:limit]
    shown_ready = ready[: limit - len(shown_underway)]

    def summary(t: Task) -> dict[str, Any]:
        open_subtasks = sum(1 for c in s.graph.children(t.id) if s.graph.is_open(c))
        return s.object(t, open_subtasks=open_subtasks)

    return {
        "store": str(store),
        "underway": [summary(t) for t in shown_underway],
        "ready": [summary(t) for t in shown_ready],
        "blocked": blocked,
        "omitted": len(underway) + len(ready) - len(shown_underway) - len(shown_ready),
    }


def open_in_editor(refs: Iterable[str]) -> dict[str, Any]:
    """Open the task files, or the store folder without REFS, in the editor; wait for it."""
    s = snapshot()
    refs = list(refs)
    if refs:
        paths = [s.paths[i] for i in dict.fromkeys(s.resolve(r) for r in refs)]
    else:
        store = s.location.require()
        if not store.is_dir():
            raise DocketError(f"{store} does not exist yet; create a task first")
        paths = [store]
    editor = _editor(s.config)
    files = [str(p) for p in paths]
    code = subprocess.run([*editor, *files]).returncode
    if code:
        raise DocketError(f"editor exited with {code}: {editor[0]}")
    return {"editor": editor, "paths": files}


def _editor(config: Config) -> list[str]:
    """The editor command from config, VISUAL, or EDITOR, with its program found on PATH."""
    line = config.editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not line:
        raise DocketError(f"no editor set; add 'editor: zed' to {user_home() / 'config.yaml'}")
    if os.name == "nt":
        # Windows paths keep their backslashes; drop the quotes around a spaced path.
        argv = [a.removeprefix('"').removesuffix('"') for a in shlex.split(line, posix=False)]
    else:
        argv = shlex.split(line)
    program = shutil.which(argv[0]) if argv else None
    if program is None:
        raise DocketError(f"editor not found: {line}")
    return [program, *argv[1:]]


def where(location: Location | None = None) -> dict[str, str | None]:
    location = location or discover()
    return {
        "store": str(location.store) if location.store else None,
        "git_dir": str(location.git_dir) if location.git_dir else None,
        "worktree": str(location.worktree) if location.worktree else None,
    }
