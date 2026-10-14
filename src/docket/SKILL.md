---
name: docket
description: Local task tracker CLI. Use when the user mentions Docket or one of its tasks, or when a session index (a `repo` or `no repo` header over doing, ready, and waiting lines) is in context.
---

# Docket

Everything goes through the `docket` CLI; never open the database. Add `--json` to any command for one JSON value (stdout stays empty on error). Exit 1 is a command error with a one-line message on stderr; exit 2 is a usage error (bad flags or arguments).

## Typical session

```
docket next --start                      # claim the oldest ready task
docket note a3f8.1 "Endpoint streams; pagination still TODO"
docket add "Fix CSV quoting" --from a3f8.1 --file plan.md   # work found along the way
docket complete a3f8.1
```

## Commands

| Command | Behavior |
|---|---|
| `next [--start]` | Oldest ready task in scope, with body and parent summary. Prints nothing (`null` with `--json`) when none. `--start` sets it to `doing` atomically, so two agents never claim the same task. |
| `show ID` | Fields and body. |
| `ls [--tag T] [--status S] [--archived]` | Tasks in scope with status and ready state. |
| `search QUERY [--archived]` | Full-text search in scope. |
| `index [--limit N]` | Session summary (what the SessionStart hook prints). |
| `add "title" [--parent ID] [--dep ID] [--tag T] [--repo KEY \| --no-repo] [--from ID] [--file PATH]` | Create a task. `--dep`, `--tag`, `--repo` repeat. `--from` records the task you were working on when you found this one. Prints the ID and repos. |
| `set ID FIELD VALUE` | Fields: `title`, `status`, `blocked_on`, `branch`, `pr`, `issue`. |
| `set ID tags\|repos\|links [VALUE] [--add V] [--remove V]` | `--add` and `--remove` repeat; a bare comma-separated VALUE replaces the whole list. |
| `note ID "text"` | Append a timestamped line to the body. This is how an agent adds to a body. |
| `edit ID` | Opens the body in `$VISUAL` or `$EDITOR` for the user to edit; errors if neither is set. |
| `dep ID DEP_ID` / `undep ID DEP_ID` | Add or remove a dependency. |
| `attach ID PATH...` | Copy files into the task's attachments and add them to `links`. |
| `complete ID` / `cancel ID` / `reopen ID` | Archive as done, archive as canceled, return to open. |
| `export [--format jsonl\|md] [--out DIR]` | Dump all tasks. `jsonl` goes to stdout, or `DIR/docket.jsonl` with `--out`; `md` requires `--out`. |
| `where`, `doctor` | Show store and repo key, check store health. |

## Concepts

- **IDs and hierarchy**: top-level is four characters (`a3f8`); children append a number (`a3f8.1`, `a3f8.1.1`). Any unique prefix works; an ambiguous prefix errors and lists the matches. A child needs an open parent.
- **Scope**: inside a git repo, `ls`, `search`, and `index` show that repo's tasks plus tasks with no repo, and `next` only that repo's tasks. Outside a repo, only tasks with no repo. `--repo KEY` picks another repo (`owner/repo`, or a bare name that matches one known repo); `--all-repos` removes the filter.
- **Status**: open tasks are `todo`, `doing`, `review`, or `blocked`. `blocked_on` records what a blocked task waits on: set the status to `blocked` first, and setting any other status clears it. Archived tasks take neither `status` nor `blocked_on`.
- **Archiving**: `complete` and `cancel` archive a task (outcome `done` or `canceled`) and clear its status. A parent can't be completed or canceled while it has open children, and `cancel` is refused while an open task depends on it; the error lists them. `reopen` returns a task to `doing`, and fails on a child whose parent is archived or when it would create a dependency cycle. Tasks are never deleted; `ls` and `search` hide archived ones unless given `--archived`.
- **Dependencies and readiness**: a dependency is satisfied only when its target is archived as `done`. A task also waits on every ancestor's dependencies. `dep` and `add --dep` reject cycles; `dep` also rejects archived tasks on either side. A task is ready when it is `todo`, has no unsatisfied dependencies, and has no open children, so `next` returns leaf work.

## Gotchas

- `next` without `--start` only reads; the task stays `todo` and another agent can claim it.
- `add` reads its body from stdin whenever stdin is piped and waits for EOF, so a shell that leaves a pipe open hangs it. Pass the body with `--file PATH`, or redirect stdin from an empty source.
- Write multi-line bodies to a file for `--file`; quoted arguments mangle them. Literal newlines are kept, and `\n` stays two characters.
- Subtasks share their root task's attachment folder.
- A child does not inherit its parent's repos.
