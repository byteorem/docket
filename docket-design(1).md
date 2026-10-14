# Docket Design

Status: design (not built)
Updated: 2026-10-13

Docket is a personal, local task tracker CLI for one developer and the few coding agents working alongside them. It tracks tasks, their status, dependencies, and attachments across repos.

All task data lives in one SQLite database at `~/.docket/docket.db`, outside all repos. The database is the source of truth. People and agents interact with it only through the `docket` CLI.

## Store

```
~/.docket/
├── docket.db                  # source of truth
├── backups/
│   └── docket-YYYY-MM-DD.db   # daily copies
└── attachments/
    └── <task-id>/
        └── <file>
```

- Folders and the database are created on first use. No initialization is required.
- Nothing is written into repos.
- `DOCKET_HOME` overrides the store path.
- The store must not live in OneDrive or any other synced folder.

### Repo key

- Inside a git repo, the key is the last two path segments of the `origin` URL without `.git`: `https://ghe.company.com/billing/api.git` and `git@ghe.company.com:billing/api.git` both give `billing/api`.
- Every worktree and subfolder of a repo resolves to the same key.
- With no `origin`, the key is the main worktree's folder name, and the CLI prints a warning.
- `--repo KEY` overrides detection on any command. It accepts `owner/repo`, or a bare `repo` when that matches exactly one known key.
- Every write prints the repos the task landed in.

### Scope

| Command | Inside a repo | Outside a repo |
|---|---|---|
| `next` | Tasks whose repos include the current key | Tasks with no repos |
| `ls`, `search`, `index` | Current key, plus tasks with no repos | Tasks with no repos |

`--repo KEY` selects another repo. `--all-repos` removes the filter.

## Tasks

A task has a title, a free-form Markdown body, and the fields below.

| Field | Meaning |
|---|---|
| `id` | Task ID |
| `title` | Human-readable title |
| `body` | Free-form Markdown |
| `status` | Open tasks only: `todo`, `doing`, `review`, or `blocked`; `todo` on creation |
| `blocked_on` | What the task waits on while `status` is `blocked` |
| `repos` | Repo keys the task touches; empty for work outside any repo |
| `depends_on` | Tasks that must be completed first |
| `discovered_from` | Task being worked on when this one was found |
| `tags` | Labels for `ls --tag` |
| `branch`, `pr`, `issue` | References |
| `links` | Task IDs, attachment paths, or URLs |
| `created` | UTC timestamp |
| `outcome` | Archived tasks only: `done` or `canceled` |
| `archived` | Archived tasks only: UTC timestamp |

### IDs

- A top-level ID is four random lowercase letters or digits (`a3f8`), checked unused.
- A child ID appends the next sequential number to its parent's ID: `a3f8.1`, `a3f8.2`, `a3f8.1.1`.
- The parent is derived from the ID and never changes.
- Commands accept any unique ID prefix. An ambiguous prefix is an error that lists the matches.

### Lifecycle

- `complete` and `cancel` archive a task: they clear `status` and `blocked_on`, and set `outcome` and `archived`.
- `reopen` clears `outcome` and `archived`, and sets `status: doing`.
- Default `ls` and `search` exclude archived tasks. `--archived` includes them.
- Tasks are never deleted.

### Relationships

- A dependency is satisfied when its target is archived with `outcome: done`.
- A task waits on its own dependencies and on every ancestor's dependencies.
- `dep` rejects missing and archived targets.
- `cancel` is rejected while any open task depends on the task. The error lists those tasks.
- Dependencies and parents may cross repos. A child does not inherit its parent's repos.
- A child requires an open parent. A parent cannot be completed or canceled while it has open children. Reopening a child whose parent is archived is an error.

**Cycle rule.** A dependency of A on B is rejected when B reaches A or any descendant of A, counting B itself and following dependencies, inherited ancestor dependencies, and parent-to-child edges. This one rule rejects self-dependencies, dependencies between a task and its own ancestors or descendants, and indirect cycles.

### Readiness

A task is **ready** when it is open, `status` is `todo`, every dependency of the task and of its ancestors is satisfied, and it has no open children.

`next` returns the oldest ready task in scope by `created`, ties broken by ID, with its full body and a parent summary. When nothing is ready, it prints nothing (or `null` with `--json`) and exits 0.

`next --start` sets the returned task to `status: doing` in the same transaction, so two agents never start the same task. Without `--start`, `next` only reads.

### Commands

List commands accept `--repo KEY` and `--all-repos`.

| Command | Behavior |
|---|---|
| `add "title" [--parent ID] [--dep ID] [--tag T] [--repo KEY \| --no-repo] [--from ID] [--file PATH]` | Create a task. Body from `--file` or stdin, else empty. `--dep`, `--tag`, `--repo` are repeatable. `--from` sets `discovered_from`. Prints ID and repos. |
| `show ID` | Print a task with its fields and body. |
| `ls [--tag T] [--status S] [--archived]` | List tasks in scope with status and ready state. |
| `next [--start]` | Print the oldest ready task in scope. `--start` also sets it to `doing`. |
| `set ID FIELD VALUE` | Set `title`, `status`, `blocked_on`, `branch`, `pr`, or `issue`. Setting `status` to anything but `blocked` clears `blocked_on`. |
| `set ID FIELD [VALUE] [--add V] [--remove V]` | For `tags`, `repos`, and `links`: `--add` and `--remove` are repeatable; a bare comma-separated value replaces the list. |
| `edit ID` | Open the body in `$VISUAL` or `$EDITOR` through a temp file, and save it on exit. Errors if neither is set. |
| `note ID "text"` | Append a timestamped line to the body. |
| `dep ID DEP_ID` | Add a dependency. |
| `undep ID DEP_ID` | Remove a dependency. |
| `attach ID PATH...` | Copy files into the task's attachment folder and add each to `links`. |
| `complete ID` | Archive with `outcome: done`. |
| `cancel ID` | Archive with `outcome: canceled`. |
| `reopen ID` | Return an archived task to open. |
| `search QUERY [--archived]` | Full-text search in scope. |
| `index [--limit N]` | Print the session summary. |
| `export [--format jsonl\|md] [--out DIR]` | Dump all tasks. `jsonl`: one task per line with every field. `md`: one file per task, a metadata block followed by the body. |
| `where` | Print the store path, current repo key, and the remote it came from. |
| `doctor` | Run SQLite integrity and foreign-key checks; report attachment folders with no task and attachment links to missing files. |
| `install skill [--dir PATH]` | Install the bundled agent skill. See [Agent integration](#agent-integration). |
| `install hook` | Add the SessionStart hook and `docket` permission rules to Claude Code user settings. See [Agent integration](#agent-integration). |

## Attachments

- Files live in `attachments/<task-id>/`, keyed by the top-level task ID. Subtasks use their root task's folder.
- `attach` copies files and never moves them.
- Links store the store-relative path: `attachments/a3f8/spec.xlsx`.
- A name collision within a folder is an error.
- Backups do not include attachments.

## Session summary

`index` prints a compact summary for an agent at session start.

- Open tasks in scope with ID, title, and status: `doing` and `review` first, then ready, then a count of the rest.
- Capped at 20 tasks by default, with a count of anything omitted.
- Always exits 0.

```
repo billing/api
  doing   k2p9    Fix auth redirect
  ready   a3f8.1  Add CSV export endpoint
  +5 waiting
```

## Agent integration

Agents use the CLI like any other shell tool. They never query the database directly.

### Skill

The package bundles `SKILL.md`, which teaches the commands, ID prefixes, scope, statuses, and archiving. It covers how to use Docket, not when a workflow should create or close tasks. It tells agents to pass long bodies with `--file`.

`install skill` writes it to `~/.claude/skills/docket/SKILL.md`, or to `<PATH>/docket/SKILL.md` with `--dir PATH`.

- It creates the folder if needed and overwrites an existing `docket/SKILL.md`.
- It never touches other skills.
- It prints the path it wrote.

### Hook and permissions

`install hook` adds a SessionStart handler and two permission rules to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["Bash(docket *)", "PowerShell(docket *)"]
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "docket", "args": ["index"] }
        ]
      }
    ]
  }
}
```

- The handler has no matcher, so it runs on startup, resume, `/clear`, and compaction. Claude Code adds its plain-text stdout to the agent's context.
- The handler uses exec form (`args`), so it runs the same from Git Bash and PowerShell.
- The permission rules let agents run `docket` commands without prompting.
- The command creates the file if it is missing and preserves every other key, hook, and rule.
- It adds only the entries that are missing. When all of them exist, it changes nothing and says so.
- Before writing, it copies the original to `settings.json.bak`. It writes atomically.
- If the existing file is not valid JSON, it exits with an error and leaves the file unchanged.

A typical session:

```
docket next --start
docket note a3f8.1 "Endpoint streams; pagination still TODO"
docket add "Fix CSV quoting for commas" --from a3f8.1 --file plan.md
docket complete a3f8.1
```

## Database

### Connection

Every connection sets:

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 10000;
PRAGMA foreign_keys = ON;
```

Every write command runs in one `BEGIN IMMEDIATE` transaction. Validation runs inside the transaction after the change is applied, and a violation rolls everything back.

### Schema

```sql
CREATE TABLE tasks (
  id              TEXT PRIMARY KEY,
  parent_id       TEXT REFERENCES tasks(id),
  title           TEXT NOT NULL,
  body            TEXT NOT NULL DEFAULT '',
  status          TEXT CHECK (status IN ('todo', 'doing', 'review', 'blocked')),
  blocked_on      TEXT,
  discovered_from TEXT REFERENCES tasks(id),
  branch          TEXT,
  pr              TEXT,
  issue           TEXT,
  created         TEXT NOT NULL,
  outcome         TEXT CHECK (outcome IN ('done', 'canceled')),
  archived        TEXT,
  CHECK ((archived IS NULL     AND status IS NOT NULL AND outcome IS NULL)
      OR (archived IS NOT NULL AND status IS NULL     AND outcome IS NOT NULL))
);

CREATE TABLE task_repos (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  repo    TEXT NOT NULL,
  PRIMARY KEY (task_id, repo)
);

CREATE TABLE task_tags (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  tag     TEXT NOT NULL,
  PRIMARY KEY (task_id, tag)
);

CREATE TABLE task_links (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  link    TEXT NOT NULL,
  PRIMARY KEY (task_id, link)
);

CREATE TABLE deps (
  task_id TEXT NOT NULL REFERENCES tasks(id),
  dep_id  TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, dep_id)
);

CREATE VIRTUAL TABLE tasks_fts USING fts5(id UNINDEXED, title, body);
-- kept in sync with tasks by insert, update, and delete triggers

CREATE VIEW ready AS
WITH RECURSIVE lineage(task_id, ancestor_id) AS (
  SELECT id, id FROM tasks WHERE archived IS NULL
  UNION ALL
  SELECT l.task_id, t.parent_id
  FROM lineage l JOIN tasks t ON t.id = l.ancestor_id
  WHERE t.parent_id IS NOT NULL
)
SELECT t.* FROM tasks t
WHERE t.archived IS NULL
  AND t.status = 'todo'
  AND NOT EXISTS (
    SELECT 1 FROM lineage l
    JOIN deps d  ON d.task_id = l.ancestor_id
    JOIN tasks x ON x.id = d.dep_id
    WHERE l.task_id = t.id
      AND (x.archived IS NULL OR x.outcome <> 'done'))
  AND NOT EXISTS (
    SELECT 1 FROM tasks c WHERE c.parent_id = t.id AND c.archived IS NULL);
```

- The cycle rule runs as a recursive CTE over `deps`, inherited ancestor dependencies, and open parent-to-child edges.
- Search quotes each query term, ANDs them, and passes the result to FTS5. Results rank by bm25, weighting title over body.

### Migrations

- `PRAGMA user_version` holds the schema version.
- Migrations are ordered functions in `db.py`. On open, the CLI takes a backup, then applies every migration above the current version in one transaction.
- A database newer than the CLI is an error.

### Backups

- Before the first write of each UTC day, the CLI copies `docket.db` to `backups/docket-YYYY-MM-DD.db` with the SQLite backup API.
- The 14 most recent backups are kept, and older ones are deleted.

## Implementation

### Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Project and install | uv: `pyproject.toml` with a `docket` console script, installed with `uv tool install` |
| CLI framework | Typer |
| Database | `sqlite3` with FTS5 |
| Tests and lint | pytest and ruff, dev only |

Typer is the only runtime dependency. Phase 1 confirms the uv-installed interpreter has FTS5, and the CLI fails with a clear message if it doesn't. Module-level imports stay light so commands start fast.

### Layout

```
docket/
├── pyproject.toml
├── src/docket/
│   ├── __main__.py
│   ├── cli.py          Typer app, text and JSON output
│   ├── repo.py         repo key resolution
│   ├── db.py           connection, schema, migrations, backups
│   ├── tasks.py        task commands, relationship and cycle rules
│   ├── checks.py       doctor, export
│   ├── install.py      install skill, install hook
│   └── SKILL.md        bundled agent skill (package data)
└── tests/
```

Command logic returns plain data. Only `cli.py` formats text or JSON.

### Repo key resolution

- `git rev-parse --show-toplevel` finds the worktree root.
- `git remote get-url origin` gives the URL. The key is the last two segments after splitting on `/` and `:`, with `.git` stripped.
- With no `origin`, the key is the folder name of the main worktree (the parent of `git rev-parse --path-format=absolute --git-common-dir`).

### Conventions

- Timestamps are UTC with second precision: `2026-10-11T23:08:00Z`.
- stdout and stderr are reconfigured to UTF-8 at startup.
- Literal newlines in quoted arguments are preserved. `\n` escapes are never converted.

### Output

- Default output is compact plain text. Every command supports `--json`, printing one JSON value.
- Exit codes: 0 success; 1 command error (not found, ambiguous prefix, rule violation, database busy); 2 usage error.
- Errors go to stderr. In `--json` mode, stdout stays empty on error.
- Command errors print one concise line to stderr and raise `typer.Exit(1)`. Typer's pretty exceptions are disabled, so unexpected errors never print locals.

### Testing

- `DOCKET_HOME` points at a temp folder. Throwaway git repos have fake `origin` remotes.
- Cover repo key cases (HTTPS, SSH, worktree, subfolder, no origin, bare-name matching), every relationship rule, each cycle shape (self, ancestor, descendant, inherited, indirect), readiness, archive and reopen, migrations from every prior version, backups, and two processes writing at once.
- Cover `install hook` with a missing settings file, existing unrelated hooks and rules, an existing Docket handler or rule, and invalid JSON. Point the home directory at a temp folder.
- Run the suite on Windows.
- `uv run pytest`, `uv run ruff check`.

## Build phases

1. **Database and repo key.** Goal: `where` resolves in every case, the schema creates and migrates, and FTS5 is confirmed.
2. **Task commands.** Goal: a full lifecycle runs through the CLI with every relationship and cycle rule enforced.
3. **Maintenance.** Goal: backups, `export`, `edit`, and `doctor` work.
4. **Agent integration.** Goal: `install skill` and `install hook` set up a machine, and an agent starts each session with `index` in context.

## Open

- IT approval for uv, and for installing Typer from PyPI or an internal mirror.
