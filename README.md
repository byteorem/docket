# docket

> Local task tracker for you and your coding agents

Each repo keeps its tasks in `.git/docket/` as Markdown files with YAML frontmatter, so git never tracks them and every worktree shares them. Docket owns identity, status, dependencies, and subtasks; any other frontmatter field is yours. There is no index or daemon: every command reads the files directly.

## Install

```sh
uv tool install git+https://github.com/byteorem/docket
```

Requires [uv](https://docs.astral.sh/uv/) and Git; uv fetches Python 3.12 if it's missing. To update to the latest `main`, run the same command with `--force`. From a clone, `uv tool install .` installs the working tree instead.

## Usage

```sh
docket create "Add CSV export endpoint" --set branch=feat/csv   # created v4ek
docket create "Stream rows" --parent v4ek                       # created v4ek.1
docket set v4ek.1 status in_progress
docket note v4ek.1 "Pagination still TODO"
docket body v4ek.1 --section Plan --append "- [ ] paginate"
docket close v4ek.1
```

| Commands | |
|---|---|
| `create` `show` `path` `open` | Create and read tasks; `open` launches your editor |
| `ls` `ready` `blocked` | List by state, filtered with `--status` and `--where K=V` |
| `set` `unset` `add` `remove` | Change fields |
| `note` `body` | Log a timestamped note under `## Notes`, or edit the body or one `## Section` of it |
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
├── .lock           locked during a write
└── tasks/<id>.md
```

Docket finds the store from the current folder, following a linked worktree's `.git` file to the main repo's git directory. `DOCKET_DIR` points it at any folder instead. Deleting or recloning the repo deletes its tasks.

`config.yaml` in the store overrides `~/.docket/config.yaml` (or `$DOCKET_HOME/config.yaml`):

```yaml
default_status: open         # set by create and reopen
closed_statuses: [closed]    # close sets the first; any other status is open
editor: zed                  # used by open; default $VISUAL, then $EDITOR
```

`docket open ID...` passes the task files to the editor, and `docket open` alone passes the store folder. The editor is a command line, so it can take flags (`editor: code --new-window`), and docket waits for it to exit.
