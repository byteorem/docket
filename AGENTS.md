# docket

Local task tracker CLI: Python 3.12, `argparse`, tasks as Markdown files with YAML frontmatter under `<repo>/.git/docket/`. PyYAML is the only runtime dependency; keep it that way. Docket never runs `git`; discovery reads `.git` files directly.

## Checks

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

Windows is the primary platform; the suite must pass there.

## Layout rules

- `store.py` (discovery, config, lock, atomic write), `tasks.py` (file format, IDs, field rules, commands), `graph.py` (states, deps, subtasks, cycles), `checks.py`, and `install.py` (skill and Claude Code hook) return plain data. Only `cli.py` formats text or JSON, through `emit`.
- `cli.py` imports `docket` modules inside each command, not at module level, so startup stays fast. The SessionStart hook runs `docket index` on every session.
- A command error is `raise DocketError("one line")`; `cli.run` prints it to stderr and returns 1. Usage errors come from argparse (exit 2). With `--json`, stdout stays empty on error.
- `index` swallows every exception: it runs from the hook and must always exit 0.

## Task files

- Every write goes through `tasks.writing()`: it takes the store lock and snapshots the store, so rule checks and the write see the same state. Files are written with `store.atomic_write`.
- `tasks.dump` owns the frontmatter format: core fields first in `CORE` order, user fields in their existing order, IDs double-quoted, empty fields omitted. The body after the closing fence is preserved byte for byte.
- The YAML loader and dumper drop the timestamp resolver, so `created` and other dates stay strings.
- Tasks are never deleted by the CLI.

## Tests

- Use the fixtures in `tests/conftest.py`: `store` (a `DOCKET_DIR` store), `git_repo` for throwaway repos, and `run`, `ok`, `fail`, `mk` to invoke the CLI in-process. The autouse `isolated` fixture points `DOCKET_HOME` at a temp folder and clears `DOCKET_DIR`; never let a test reach the real `~/.docket`. Install tests point `HOME` and `USERPROFILE` at a temp folder so they never touch the real `~/.claude`.

## Docs

`src/docket/SKILL.md` ships in the package and is the agent-facing contract; `docket install skill` copies it out. When a command's flags, output, or rules change, update `SKILL.md` and `README.md` in the same change.
