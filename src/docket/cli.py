"""Argument parsing and text/JSON output. Command logic lives in the other modules."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

DESCRIPTION = "Local task tracker. Each repo keeps its tasks in .git/docket as Markdown files."
EPILOG = "Every command takes --json. Run `docket help COMMAND` for a command's flags."


def emit(data: Any, text: str | Callable[[], str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return
    out = text() if callable(text) else text
    if out:
        print(out)


def _written(action: str, task: dict[str, Any], as_json: bool) -> None:
    emit({"task": task}, f"{action} {task['id']}  {task['title']}", as_json)


def _warn_invalid(s: Any) -> None:
    if s.invalid:
        count = len(s.invalid)
        files = "file" if count == 1 else "files"
        print(f"warning: skipped {count} invalid task {files}; run docket check", file=sys.stderr)


def _rows(tasks: list[dict[str, Any]], tree: bool = False) -> str:
    """One line per task: ID, state, status, title."""
    from docket.graph import parent_id

    ids = {t["id"] for t in tasks}

    def label(t: dict[str, Any]) -> str:
        depth = 0
        ancestor = t["parent"] if tree else None
        while ancestor is not None:
            depth += ancestor in ids
            ancestor = parent_id(ancestor)
        return "  " * depth + t["id"]

    labels = [label(t) for t in tasks]
    id_width = max(map(len, labels), default=0)
    state_width = max((len(t["state"]) for t in tasks), default=0)
    status_width = max((len(t["status"]) for t in tasks), default=0)
    lines = []
    for name, t in zip(labels, tasks, strict=True):
        line = f"{name:<{id_width}}  {t['state']:<{state_width}}  {t['status']:<{status_width}}"
        line += f"  {t['title']}"
        if "blockers" in t:
            line += f"  (waiting on {', '.join(t['blockers'])})"
        lines.append(line)
    return "\n".join(lines)


# --- commands ---


def cmd_create(args: argparse.Namespace) -> None:
    from docket import tasks

    task = tasks.create(
        args.title,
        parent=args.parent,
        deps=args.dep,
        fields=args.set,
        body=_read_text(args.file) if args.file else "",
    )
    _written("created", task, args.json)


def cmd_show(args: argparse.Namespace) -> None:
    from docket import tasks

    if args.section is not None:
        section = tasks.show_section(tasks.snapshot(), args.id, args.section)
        emit(section, section["text"], args.json)
        return
    data = tasks.show(tasks.snapshot(), args.id)

    def text() -> str:
        task, related = data["task"], data["related"]
        out = [data["file"].rstrip("\n")]
        sections = {
            "Parent": [task["parent"]] if task["parent"] else [],
            "Blockers": task["blockers"],
            "Blocking": task["blocking"],
            "Subtasks": task["subtasks"],
        }
        for heading, ids in sections.items():
            if not ids:
                continue
            found = [related[i] for i in ids if related[i] is not None]
            lines = _rows(found).splitlines() if found else []
            lines += [f"{i}  missing" for i in ids if related[i] is None]
            out += ["", f"{heading}:", *(f"  {line}" for line in lines)]
        return "\n".join(out)

    emit(data["task"], text, args.json)


def cmd_path(args: argparse.Namespace) -> None:
    from docket import tasks

    s = tasks.snapshot()
    path = str(s.paths[s.resolve(args.id)])
    emit({"path": path}, path, args.json)


def cmd_open(args: argparse.Namespace) -> None:
    from docket import tasks

    emit(tasks.open_in_editor(args.ids), "", args.json)


def _list(args: argparse.Namespace, states: tuple[str, ...], tree: bool = False) -> None:
    from docket import tasks

    s = tasks.snapshot()
    found = tasks.select(s, states=states, status=args.status, where=args.where, tree=tree)
    _warn_invalid(s)
    emit(found, lambda: _rows(found, tree), args.json)


def cmd_ls(args: argparse.Namespace) -> None:
    states = ("ready", "blocked", "closed") if args.all else ("ready", "blocked")
    _list(args, states, args.tree)


def cmd_ready(args: argparse.Namespace) -> None:
    _list(args, ("ready",))


def cmd_blocked(args: argparse.Namespace) -> None:
    _list(args, ("blocked",))


def cmd_set(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("updated", tasks.set_field(args.id, args.key, args.value), args.json)


def cmd_unset(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("updated", tasks.unset_field(args.id, args.key), args.json)


def cmd_add(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("updated", tasks.add_value(args.id, args.key, args.value), args.json)


def cmd_remove(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("updated", tasks.remove_value(args.id, args.key, args.value), args.json)


def cmd_close(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("closed", tasks.close(args.id, status=args.status, force=args.force), args.json)


def cmd_reopen(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("reopened", tasks.reopen(args.id, force=args.force), args.json)


def cmd_dep(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("updated", tasks.dep(args.id, args.dep_id), args.json)


def cmd_undep(args: argparse.Namespace) -> None:
    from docket import tasks

    _written("updated", tasks.undep(args.id, args.dep_id), args.json)


def cmd_note(args: argparse.Namespace) -> None:
    from docket import tasks

    text = _read_text("-") if args.text == "-" else args.text
    _written("noted", tasks.note(args.id, text), args.json)


def cmd_body(args: argparse.Namespace) -> None:
    from docket import tasks

    old = ""
    if args.append is not None:
        mode, text = "append", args.append
    elif args.replace is not None:
        mode, (old, text) = "replace", args.replace
    else:
        mode, text = "set", _read_text(args.file)
    task = tasks.edit_body(args.id, mode, text, old=old, section=args.section)
    _written("updated", task, args.json)


def cmd_where(args: argparse.Namespace) -> None:
    from docket import tasks

    data = tasks.where()
    emit(
        data,
        lambda: "\n".join(f"{k:<9} {v or '-'}" for k, v in data.items()),
        args.json,
    )


def cmd_index(args: argparse.Namespace) -> None:
    """Runs from the SessionStart hook, so it reports any failure and still exits 0."""
    try:
        from docket import tasks

        s = tasks.snapshot()
        data = tasks.index(s, args.limit)
        if data is None:
            return

        def text() -> str:
            listed = data["underway"] + data["ready"]
            id_width = max((len(t["id"]) for t in listed), default=0)
            title_width = max((len(t["title"]) for t in listed), default=0)
            status_width = max((len(t["status"]) for t in listed), default=0)
            lines = [f"store {data['store']}"]
            for t in listed:
                line = f"  {t['state']:<8} {t['id']:<{id_width}}  {t['title']:<{title_width}}"
                line += f"  {t['status']:<{status_width}}"
                count = t["open_subtasks"]
                if count:
                    line += f"  ({count} open subtask{'' if count == 1 else 's'})"
                lines.append(line.rstrip())
            if data["omitted"]:
                lines.append(f"  +{data['omitted']} more")
            if data["blocked"]:
                lines.append(f"  +{data['blocked']} blocked")
            return "\n".join(lines)

        _warn_invalid(s)
        emit(data, text, args.json)
    except Exception as e:
        print(f"docket index: {e}", file=sys.stderr)


def cmd_check(args: argparse.Namespace) -> int:
    from docket import checks, tasks

    data = checks.check(tasks.snapshot())
    problems = data["problems"]
    emit(data, lambda: "\n".join(f"{p['kind']}: {p['message']}" for p in problems), args.json)
    return 1 if problems else 0


def cmd_install_skill(args: argparse.Namespace) -> None:
    from pathlib import Path

    from docket import install

    data = install.install_skill(Path(args.dir) if args.dir else None)
    emit(data, f"wrote {data['path']}", args.json)


def cmd_install_hook(args: argparse.Namespace) -> None:
    from docket import install

    data = install.install_hook()

    def text() -> str:
        if not data["added"]:
            return f"already installed; nothing changed\n{data['path']}"
        lines = [f"added {a}" for a in data["added"]]
        lines.append(f"settings {data['path']}")
        if data["backup"]:
            lines.append(f"backup   {data['backup']}")
        return "\n".join(lines)

    emit(data, text, args.json)
    warning = install.path_warning()
    if warning:
        print(warning, file=sys.stderr)


# --- parsing ---


def _pair(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got '{text}'")
    return key, value


def _count(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"expected a whole number, got '{text}'")
    return int(text)


def _read_text(path: str) -> str:
    """The text in PATH, or stdin when PATH is -. Stdin is never read unasked."""
    from pathlib import Path

    from docket.store import DocketError

    try:
        if path == "-":
            return sys.stdin.read()
        return Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise DocketError(f"{'stdin' if path == '-' else path}: not valid UTF-8") from None


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print one JSON value")
    parser = argparse.ArgumentParser(
        prog="docket", description=DESCRIPTION, epilog=EPILOG, allow_abbrev=False
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(name: str, handler: Callable[..., Any], summary: str) -> argparse.ArgumentParser:
        p = sub.add_parser(
            name, help=summary, description=summary, parents=[common], allow_abbrev=False
        )
        p.set_defaults(handler=handler)
        return p

    def filters(p: argparse.ArgumentParser) -> None:
        p.add_argument("--status", metavar="S", help="only tasks with status S")
        p.add_argument(
            "--where",
            metavar="K=V",
            type=_pair,
            action="append",
            default=[],
            help="only tasks whose field K is V or a list containing V (repeatable)",
        )

    p = command("create", cmd_create, "Create a task, with a body from --file. Prints the ID.")
    p.add_argument("title")
    p.add_argument("--parent", metavar="ID", help="create a subtask of ID")
    p.add_argument("--dep", metavar="ID", action="append", default=[], help="repeatable")
    p.add_argument(
        "--set", metavar="K=V", type=_pair, action="append", default=[], help="repeatable"
    )
    p.add_argument("--file", metavar="PATH", help="read the body from PATH, or stdin for -")

    p = command("show", cmd_show, "Print the task file, then parent, blockers, and subtasks.")
    p.add_argument("id")
    p.add_argument("--section", metavar="NAME", help="print only the text under heading NAME")

    p = command("path", cmd_path, "Print the task file's absolute path.")
    p.add_argument("id")

    p = command("open", cmd_open, "Open task files, or the store folder, in your editor.")
    p.add_argument("ids", metavar="ID", nargs="*")

    p = command("ls", cmd_ls, "List open tasks.")
    filters(p)
    p.add_argument("--all", action="store_true", help="include closed tasks")
    p.add_argument("--tree", action="store_true", help="indent subtasks under their parent")

    p = command("ready", cmd_ready, "List ready tasks: open, with every dep closed.")
    filters(p)

    p = command("blocked", cmd_blocked, "List blocked tasks with their open deps.")
    filters(p)

    p = command("set", cmd_set, "Set title, status, or a user field.")
    p.add_argument("id")
    p.add_argument("key")
    p.add_argument("value")

    p = command("unset", cmd_unset, "Remove a user field.")
    p.add_argument("id")
    p.add_argument("key")

    p = command("add", cmd_add, "Add VALUE to the list field KEY.")
    p.add_argument("id")
    p.add_argument("key")
    p.add_argument("value")

    p = command("remove", cmd_remove, "Remove VALUE from the list field KEY.")
    p.add_argument("id")
    p.add_argument("key")
    p.add_argument("value")

    p = command("close", cmd_close, "Close a task with the first closed status, or --status.")
    p.add_argument("id")
    p.add_argument("--status", metavar="S", help="a closed status from config")
    p.add_argument("--force", action="store_true", help="close even with open subtasks")

    p = command("reopen", cmd_reopen, "Reopen a closed task at the default status.")
    p.add_argument("id")
    p.add_argument("--force", action="store_true", help="reopen even if the parent is closed")

    p = command("dep", cmd_dep, "Make ID wait on DEP_ID.")
    p.add_argument("id")
    p.add_argument("dep_id")

    p = command("undep", cmd_undep, "Remove a dependency.")
    p.add_argument("id")
    p.add_argument("dep_id")

    p = command("note", cmd_note, "Append a timestamped note under ## Notes.")
    p.add_argument("id")
    p.add_argument("text", help="the note, or - to read it from stdin")

    p = command("body", cmd_body, "Replace the body, append to it, or swap one exact match.")
    p.add_argument("id")
    p.add_argument("--section", metavar="NAME", help="work inside the section under heading NAME")
    edit = p.add_mutually_exclusive_group(required=True)
    edit.add_argument(
        "--file", metavar="PATH", help="replace with the text in PATH, or stdin for -"
    )
    edit.add_argument("--append", metavar="TEXT", help="add TEXT at the end")
    edit.add_argument(
        "--replace", nargs=2, metavar=("OLD", "NEW"), help="swap the one exact match of OLD"
    )

    command("where", cmd_where, "Print the store path, git directory, and worktree root.")

    p = command("index", cmd_index, "Print the session summary. Always exits 0.")
    p.add_argument("--limit", metavar="N", type=_count, default=20, help="max tasks listed")

    command("check", cmd_check, "Report broken task files, missing deps, and cycles.")

    p = sub.add_parser(
        "install",
        help="Install the agent skill or the Claude Code hook.",
        description="Install the agent skill or the Claude Code hook.",
        allow_abbrev=False,
    )
    targets = p.add_subparsers(dest="target", metavar="TARGET", required=True)

    def target(name: str, handler: Callable[..., Any], summary: str) -> argparse.ArgumentParser:
        t = targets.add_parser(
            name, help=summary, description=summary, parents=[common], allow_abbrev=False
        )
        t.set_defaults(handler=handler)
        return t

    t = target("skill", cmd_install_skill, "Write the bundled skill to <dir>/docket/SKILL.md.")
    t.add_argument("--dir", metavar="DIR", help="skills folder (default ~/.claude/skills)")
    target("hook", cmd_install_hook, "Add the SessionStart hook and docket permission rules.")

    def cmd_help(args: argparse.Namespace) -> None:
        (sub.choices[args.topic] if args.topic else parser).print_help()

    p = sub.add_parser("help", help="Print commands and usage.", add_help=False)
    p.add_argument("topic", metavar="COMMAND", nargs="?", choices=list(sub.choices))
    p.set_defaults(handler=cmd_help)
    return parser


def run(argv: list[str] | None = None) -> int:
    from docket.store import DocketError

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.handler(args) or 0
    except DocketError as e:
        print(e, file=sys.stderr)
    except OSError as e:
        print(f"{e.strerror or e}: {e.filename}" if e.filename else str(e), file=sys.stderr)
    return 1


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(run())
