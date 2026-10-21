---
name: docket
description: Local task tracker CLI. Use when the user mentions Docket or one of its tasks, or when a session summary (a `store <path>` line over ready and blocked task lines) is in context.
---

# Docket

Each repo keeps its tasks as Markdown files in `.git/docket/tasks/`, shared by every worktree. Change tasks through the `docket` CLI, which enforces the rules below; reading a file directly (`docket path ID`) is fine, and editing one is the fallback (see Editing by hand). Add `--json` to any command for one JSON value (stdout stays empty on error). Exit 1 is a command error with a one-line message on stderr; exit 2 is a usage error.

## Typical session

```
docket ready                                   # what can be worked on now
docket set a3f8.1 status in_progress
docket note a3f8.1 "Endpoint streams; pagination still TODO"
docket body a3f8.1 --section Plan --append "- [ ] paginate"
docket create "Fix CSV quoting" --file plan.md --set found_in=a3f8.1
docket close a3f8.1
```

## Commands

| Command | Behavior |
|---|---|
| `create "title" [--parent ID] [--dep ID] [--set K=V] [--file PATH]` | Create a task, or a subtask with `--parent`. Body from `--file` or stdin. `--dep` and `--set` repeat. |
| `show ID [--section NAME]` / `path ID` | Print the file plus Parent, Blockers, Blocking, Subtasks, or with `--section` only the text under that heading / print the file's path. |
| `open [ID...]` | Open the task files, or the store folder, in the user's editor (`editor` in config, else `$VISUAL`, `$EDITOR`). For people; agents read files with `path`. When the user asks to open their tasks without naming one, run `open` with no IDs rather than listing them. |
| `ls [--status S] [--where K=V] [--all] [--tree]` | Open tasks; `--all` adds closed. `--where` repeats and matches a string, or a list containing the value. |
| `ready` / `blocked` | Same filters. Ready: open with every dep closed. Blocked: waiting on an open or missing dep. |
| `set ID KEY VALUE` / `unset ID KEY` | Set `title`, `status`, or any user field / remove a user field. |
| `add ID KEY VALUE` / `remove ID KEY VALUE` | Edit a list user field. |
| `close ID [--status S] [--force]` / `reopen ID [--force]` | Close with the first closed status (or `S`) / return to the default status. |
| `dep ID DEP_ID` / `undep ID DEP_ID` | ID waits on DEP_ID / stop waiting. |
| `note ID ["text"]` | Log what happened: append `**<timestamp>** text` under `## Notes`, adding the heading at the end if missing. Text from stdin if omitted. |
| `body ID [--section NAME] [--file PATH \| --append TEXT \| --replace OLD NEW]` | Edit the description and plan: replace the body (from `--file` or stdin), append to it, or swap the one exact match of OLD for NEW. With `--section`, work inside that section; replace and append add a missing one as `## NAME` at the end. The text can't be empty, so a body or section can be rewritten but not cleared. |
| `index [--limit N]` | Session summary: underway tasks, ready tasks, blocked count. |
| `where`, `check`, `help` | Store location; report broken or non-UTF-8 files, missing deps, cycles; usage. |
| `install skill [--dir DIR]` / `install hook` | Write this skill to `DIR/docket/`, by default in Claude Code's skills folder / add the SessionStart hook and permission rules to Claude Code settings. |

## Rules

- **IDs**: top-level is four characters (`a3f8`); subtasks append a number (`a3f8.1`, `a3f8.1.1`). Any unique prefix works, and an exact ID wins over longer IDs it prefixes.
- **Status** is any string. Only statuses in `closed_statuses` (default `[closed]`) count as closed; `in_progress`, `review`, and the like are all open. New and reopened tasks get `default_status` (default `open`).
- **Fields**: `id`, `title`, `status`, `created`, `closed_at`, and `deps` are core. Every other key is yours, and `set` stores it as a string. Docket gives user fields no meaning.
- **Subtasks**: need an open parent. A task can't close with open subtasks, and a subtask can't reopen under a closed parent, unless you pass `--force`. Open subtasks don't affect the parent's state.
- **Sections** are Markdown headings you write (`## Plan`); docket gives them no meaning beyond `note` writing to `## Notes`. `--section` matches the heading text ignoring case and any leading `#`s and covers everything up to the next heading at the same or a higher level, so subheadings stay inside. Headings in code fences don't count, and a name used twice is an error.
- **Deps**: `dep` rejects self-dependencies, unknown IDs, and cycles. A dep on a closed task stays in the file but no longer blocks.

## Editing by hand

When no command makes the change, such as a non-string or nested field, a body rework too large for `body --replace`, or a file `docket check` reports as broken, edit the file at `docket path ID` directly. Keep what the CLI would have enforced:

- Change `status` and `deps` with `set`, `close`, `reopen`, `dep`, and `undep`, so closing dates, subtask rules, and cycle checks still apply. Leave `id`, `created`, and `closed_at` as written.
- Keep the frontmatter a YAML mapping between `---` lines, IDs double-quoted, and the file UTF-8 named after its `id`. New tasks come from `create`, which picks the ID.
- Run `docket check` afterwards; it reports broken files, missing deps, and cycles.

## Gotchas

- `ready` includes parents whose subtasks are still open. Check `show` before starting a parent.
- `set ID status closed` follows the same subtask rules as `close`, but has no `--force`; use `close --force`.
- `create`, `note`, and `body` without a flag read stdin whenever it is piped and wait for EOF, so a shell that leaves a pipe open hangs them. Pass the body with `--file`, or the note text as an argument.
- `body --replace` fails when OLD matches zero times or more than once; add surrounding text to OLD until it is unique. For large rewrites, replace the section with `--file`.
- Write multi-line bodies to a file for `--file`. Quoted arguments keep literal newlines, but `\n` stays two characters.
- Outside a git repo, reads show nothing and writes fail; set `DOCKET_DIR` to use a folder instead.
