.PHONY: install build test lint typecheck lint-imports coverage golden docs-reference fmt clean check

install:        ## editable install with every extra the gate needs
	# Every extra ci.yml installs, so `make check` locally runs the same tests CI does.
	# `.[dev]` alone silently skipped serve, durability and observability — the whole
	# point of #142: a narrower install reads as a pass instead of as untested.
	uv pip install -e ".[dev,serve,durability,observability]"

build:          ## sdist + wheel into dist/
	uv build

test:           ## run the test suite (includes the golden replay suite)
	.venv/bin/pytest tests/ -q

lint:           ## ruff check
	.venv/bin/ruff check agentdeck/ tests/

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

docs-reference: ## regenerate docs-site/content/reference/{settings,cli}.mdx from the code
	.venv/bin/python scripts/generate_docs_reference.py

check: lint typecheck lint-imports test   ## full gate

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
