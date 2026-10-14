# docket

Local task tracker CLI: Python 3.12, Typer, stdlib `sqlite3` with FTS5. Typer is the only runtime dependency; keep it that way.

## Checks

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

Windows is the primary platform; the suite must pass there.

## Layout rules

- Command logic in `tasks.py`, `checks.py`, `install.py`, `repo.py` returns plain data. Only `cli.py` formats text or JSON, through `emit`.
- `cli.py` imports `docket` modules inside each command, not at module level, so startup stays fast. The SessionStart hook runs `docket index` on every session.
- A command error is `raise DocketError("one line")`; `@guarded` (under `@app.command()`) turns it into stderr and exit 1. Usage errors are `typer.BadParameter` (exit 2). With `--json`, stdout stays empty on error.
- `index` is not `@guarded` and swallows every exception: it runs from the hook and must always exit 0.

## Database

- Every write goes through `db.write(conn)`: daily backup, then one `BEGIN IMMEDIATE` transaction. Run rule checks inside that transaction (after the change, when the rule needs to see it, as in `reopen`), so a failed check rolls back everything.
- Schema changes are a new function appended to `db.MIGRATIONS`. Shipped migrations, including `_SCHEMA_V1`, never change; the user's live store is already at that version.
- Tasks are never deleted. Archiving sets `outcome` and `archived` and clears `status`; a table `CHECK` enforces that.

## Tests

- Use the `home` fixture (temp `DOCKET_HOME`), `git_repo` for throwaway repos with fake origins, and `run` to invoke the CLI in-process, all from `tests/conftest.py`. Never let a test reach the real `~/.docket` or `~/.claude`; install tests point `HOME` and `USERPROFILE` at a temp folder.

## Docs

`src/docket/SKILL.md` ships in the package and is the agent-facing contract; `docket install skill` copies it out. When a command's flags, output, or rules change, update `SKILL.md` and `README.md` in the same change.

The original design doc, with the reasoning behind scope, IDs, the cycle rule, and readiness, is in history: `git show "c8f03c4^:docket-design(1).md"`.
