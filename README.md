# docket

> Local task tracker for you and your coding agents

Each repo keeps its tasks in `.git/docket/` as Markdown files with YAML frontmatter, so git never tracks them and every worktree shares them. Docket owns identity, status, dependencies, and subtasks; any other frontmatter field is yours. There is no index or daemon: every command reads the files directly.

## Install

```sh
uv tool install .
```

Run it from a clone. Requires Python 3.12+.

## Usage

```sh
docket create "Add CSV export endpoint" --set branch=feat/csv   # created v4ek
docket create "Stream rows" --parent v4ek                       # created v4ek.1
docket set v4ek.1 status in_progress
docket note v4ek.1 "Pagination still TODO"
docket close v4ek.1
```

| Commands | |
|---|---|
| `create` `show` `path` | Create and read tasks |
| `ls` `ready` `blocked` | List by state, filtered with `--status` and `--where K=V` |
| `set` `unset` `add` `remove` `note` | Change fields and append to the body |
| `close` `reopen` `dep` `undep` | Status and dependencies |
| `where` `index` `check` `help` | Store location, session summary, problems, usage |
| `install` | Agent setup |

Every command takes `--json`, and `docket help COMMAND` lists its flags. The bundled [skill](src/docket/SKILL.md) is the full reference, covering IDs, subtask rules, and readiness.

## Agent setup

```sh
docket install skill
docket install hook
```

`install skill` writes the skill to `~/.claude/skills/docket/SKILL.md`. Pass `--dir` to use another skills folder.

`install hook` adds a Claude Code SessionStart hook that runs `docket index`, so each session starts with the repo's open tasks in context. It also adds permission rules that let agents run `docket` without prompting. It backs up `settings.json` first and changes nothing when already installed. `index` always exits 0 and prints nothing in a repo that has never had a task.

## Store

```
<repo>/.git/docket/
├── config.yaml     optional
├── .lock           held during a write
└── tasks/<id>.md
```

Docket finds the store from the current folder, following a linked worktree's `.git` file to the main repo's git directory. `DOCKET_DIR` points it at any folder instead. Deleting or recloning the repo deletes its tasks.

`config.yaml` in the store overrides `~/.docket/config.yaml` (or `$DOCKET_HOME/config.yaml`):

```yaml
default_status: open         # set by create and reopen
closed_statuses: [closed]    # close sets the first; any other status is open
```
