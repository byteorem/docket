# docket

> Local task tracker for you and your coding agents

Tasks live in one SQLite database outside every repo and are scoped to the repo you run `docket` from. They have subtasks, dependencies, and attachments, and `docket next --start` hands each agent the oldest ready task without two agents ever claiming the same one.

## Install

```sh
uv tool install .
```

Run it from a clone. Requires Python 3.12+ and an SQLite built with FTS5.

## Usage

```sh
docket add "Add CSV export endpoint" --tag api    # new task: v4ek
docket add "Stream rows" --parent v4ek            # subtask: v4ek.1
docket next --start                               # claim the oldest ready task
docket note v4ek.1 "Pagination still TODO"        # append to its body
docket complete v4ek.1                            # archive it as done
```

| Commands | |
|---|---|
| `add` `show` `ls` `search` `next` | Create, read, and find tasks |
| `set` `edit` `note` `attach` | Change fields, body, and attachments |
| `dep` `undep` | Make a task wait on another |
| `complete` `cancel` `reopen` | Archive and unarchive |
| `where` `index` `doctor` `export` `install` | Store, session summary, health, export, agent setup |

Every command takes `--json`, and `docket <command> --help` lists its flags. The bundled [skill](src/docket/SKILL.md) is the full reference, covering scope, readiness, and archiving rules.

## Agent setup

```sh
docket install skill
docket install hook
```

`install skill` writes the skill to `~/.claude/skills/docket/SKILL.md`. Pass `--dir` to use another skills folder.

`install hook` adds a Claude Code SessionStart hook that runs `docket index`, so each session starts with the repo's open tasks in context. It also adds permission rules that let agents run `docket` without prompting. It backs up `settings.json` first and changes nothing when already installed.

## Store

Everything lives in `~/.docket`, or `$DOCKET_HOME` when set:

```
docket.db       the database
backups/        daily copies, 14 kept
attachments/    files copied in by attach, one folder per top-level task
```

Inside a git repo, the repo key is `owner/repo` from the `origin` URL, or the folder name when there is no `origin`. `docket where` shows the key and the remote it came from.
