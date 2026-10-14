# docket

> Local task tracker for you and your coding agents

Tasks live in one SQLite database outside every repo, scoped to the repo you run `docket` from. Tasks have subtasks, dependencies, and attachments; `docket next --start` hands each agent the oldest ready task, and no two agents claim the same one.

## Install

From a clone:

```sh
uv tool install .
```

Requires Python 3.12+ with an SQLite built with FTS5.

## Usage

```
$ docket --help

 Usage: docket [OPTIONS] COMMAND [ARGS]...

 Personal local task tracker.

┌─ Commands ──────────────────────────────────────────────────────────────────┐
│ where     Print the store path, current repo key, and the remote it came    │
│           from.                                                             │
│ add       Create a task. Body from --file or stdin.                         │
│ show      Print a task with its fields and body.                            │
│ ls        List tasks in scope with status and ready state.                  │
│ next      Print the oldest ready task in scope.                             │
│ set       Set a field. Lists (tags, repos, links): a value replaces;        │
│           --add/--remove edit.                                              │
│ edit      Edit the body in $VISUAL or $EDITOR.                              │
│ note      Append a timestamped line to the body.                            │
│ dep       Add a dependency: ID waits on DEP_ID.                             │
│ undep     Remove a dependency.                                              │
│ attach    Copy files into the task's attachment folder and link them.       │
│ complete  Archive with outcome done.                                        │
│ cancel    Archive with outcome canceled.                                    │
│ reopen    Return an archived task to open (status doing).                   │
│ search    Full-text search in scope, best match first.                      │
│ index     Print the session summary. Always exits 0.                        │
│ doctor    Check store integrity; exit 1 when anything is found.             │
│ export    Dump all tasks: jsonl to stdout or DIR/docket.jsonl; md files     │
│           need --out.                                                       │
│ install   Install agent integration.                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

A typical session:

```sh
docket add "Add CSV export endpoint" --tag api
#=> a3f8  repos: billing/api
docket next --start
docket note a3f8 "Endpoint streams; pagination still TODO"
docket complete a3f8
```

Every command takes `--json`. The full command reference, including scope, readiness, and archiving rules, is the bundled [skill](src/docket/SKILL.md).

## Agent setup

```sh
docket install skill
docket install hook
```

`install skill` writes the skill to `~/.claude/skills/docket/SKILL.md` (`--dir` picks another skills folder). `install hook` adds a Claude Code SessionStart hook that runs `docket index`, so each session starts with the repo's open tasks in context, plus permission rules that let agents run `docket` without prompting. It backs up `settings.json` first and changes nothing when already installed.

## Store

Everything lives in `~/.docket`, or `$DOCKET_HOME` when set:

```
docket.db       the database
backups/        daily copies, 14 kept
attachments/    files copied in by attach, one folder per top-level task
```

Keep it out of OneDrive and other synced folders. Inside a git repo, the repo key is `owner/repo` from the `origin` URL, or the folder name when there is no `origin`. `docket where` shows both.
