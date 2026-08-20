# WIP: the log key becomes a session id

Half-landed, deliberately. The package is done and green on ruff, ty and import-linter; seven tests
are not converted yet. This file is the handover, and it is deleted by the commit that finishes.

## What this change is

`RunContext.log_key` (`session_id or run_id`) is gone. It answered "which stream do these events go
in" by encoding two different things as one string, and a store handed it could not tell a session
named after a run from that run itself.

| before | after |
|---|---|
| `log_key TEXT NOT NULL` | `session_id TEXT` (nullable: a run in no conversation belongs to no session) |
| `events_by_log (namespace, log_key, id)` | `events_by_session (namespace, session_id, id)` |
| `read(log_key, ctx)` | `read_session(ctx)` |
| `read_run(log_key, run_id, ctx)` | `read_run(ctx)` |
| `claim_resume(log_key, run_id, resumed, ctx, origin)` | `claim_resume(resumed, ctx, origin)` |
| `RunSummary.log_key` | `RunSummary.session_id` |

Every port method now takes `ctx` plus what genuinely is not identity. The context is the only thing
that says which run: a caller reading another one builds the context for it
(`replace(ctx, run_id=...)`), which the takeover write already did.

## The three defects it closes

| | |
|---|---|
| `deck.runs.get` corrupted a session id | `session_id = None if summary.log_key == summary.run_id else summary.log_key` returned `None` for a caller-chosen session id that happened to equal a run id |
| the busy message named a session that did not exist | a standalone run reported `session '<its own run id>' is held by run '<same id>'` |
| one key space for two things | a session and a standalone run could collide on one key |

## Migration

SQLite and Postgres migrate in place on open: add `session_id`, backfill
`CASE WHEN log_key = run_id THEN NULL ELSE log_key END`, drop the old index, drop the column.
Verified against a hand-built v4 file.

**Redis does not.** Its keys are shaped by the old encoding (`log:{ns}:{log_key}`), and rewriting
them means scanning the keyspace at open. A Redis event log written by 4.x is not read by 5.0.
That needs a CHANGELOG line before this merges, and it is a real decision to confirm, not an
oversight.

## What is left

Seven tests, all of them harness rather than product:

| test | why |
|---|---|
| `test_multiprocess_concurrency.py` (4) | the worker subprocesses in `concurrency_worker.py` still build store calls the old way |
| `test_redis_store.py` (2), `test_postgres_store.py` (1) | the same, in each store's own race harness |

Two more fail and are **not** this branch's: `test_model_providers.py` (2) fails on plain `dev` too,
because the repo's `.env` sets `OPENAI_BASE_URL` and pydantic-settings re-reads it after
`monkeypatch.delenv`.

## Not done

- CHANGELOG entry (including the Redis note above).
- `docs/design/run-identity.md` still describes `log_key` as a carried value.
