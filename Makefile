.PHONY: install build test lint typecheck lint-imports coverage golden docs-reference docs-impact roadmap-sync fmt clean check

install:        ## editable install with every extra the gate needs
	# Every extra ci.yml installs, so `make check` locally runs the same tests CI does.
	# `.[dev]` alone silently skipped serve, postgres and observability. The whole
	# point of #142: a narrower install reads as a pass instead of as untested.
	uv pip install -e ".[dev,serve,postgres,observability]"

build:          ## sdist + wheel into dist/
	uv build

test:           ## run the test suite (includes the golden replay suite)
	.venv/bin/pytest tests/ -q

lint:           ## ruff check
	# examples/ too: they are code a reader copies, and nothing else in the gate reads them.
	.venv/bin/ruff check agentdeck/ tests/ examples/

typecheck:      ## ty type check
	.venv/bin/ty check agentdeck

lint-imports:   ## import-linter contracts (.importlinter)
	.venv/bin/lint-imports

coverage:       ## per-module coverage — audit input for #71/#131, not part of `make check`
	# Zero coverage is evidence a module *may* be dead, never proof: skill_runtime is
	# copied into sandbox venvs and the crossrun tests run out-of-process, so both read
	# as uncovered while being load-bearing. Corroborate with grep + the import graph.
	.venv/bin/pytest tests/ -q --cov=agentdeck --cov-report=term-missing:skip-covered

golden:         ## re-record the wire + schema snapshots — deliberate, never automatic
	AGENTDECK_GOLDEN_UPDATE=1 .venv/bin/pytest tests/golden tests/core -q

docs-reference: ## regenerate the five generated docs-site files from the code
	.venv/bin/python scripts/generate_docs_reference.py

docs-impact: ## report which documentation pages this branch's source changes affect
	.venv/bin/python scripts/check_docs_impact.py --report

roadmap-sync:   ## refresh the live-status tables in docs/delivery/ from GitHub (gh required)
	.venv/bin/python scripts/sync_roadmap.py

eval-docs-agent: ## Jack grounding, exact checks only, no extra dependency (examples/jack/eval.py)
	cd examples/jack && ../../.venv/bin/python eval.py

eval-jack:      ## Jack, judged: relevancy and faithfulness beside the exact checks, one report
	# deepeval downgrades click and rich and ships posthog, so it never enters .venv.
	cd examples/jack && DEEPEVAL_TELEMETRY_OPT_OUT=YES \
	  uv run --quiet --with deepeval --python 3.12 python -m evals.run $(ARGS)

# docs-impact runs last and never fails: it is the one output of this gate that asks the reader
# to go read something, so it has to be the last thing on screen rather than pytest's scrollback.
check: lint typecheck lint-imports test docs-impact   ## full gate

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
