.PHONY: install build test lint typecheck lint-imports golden docs-reference fmt clean check

install:        ## editable install with dev extras
	uv pip install -e ".[dev]"

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

golden:         ## re-record the wire + schema snapshots — deliberate, never automatic
	AGENTDECK_GOLDEN_UPDATE=1 .venv/bin/pytest tests/golden tests/core -q

docs-reference: ## regenerate docs-site/content/reference/{settings,cli}.mdx from the code
	.venv/bin/python scripts/generate_docs_reference.py

check: lint typecheck lint-imports test   ## full gate

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
