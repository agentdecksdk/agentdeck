.PHONY: install build test lint typecheck lint-imports golden fmt clean check

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

golden:         ## re-record tests/golden snapshots — deliberate, never automatic
	AGENTDECK_GOLDEN_UPDATE=1 .venv/bin/pytest tests/golden -q

check: lint typecheck lint-imports test   ## full gate

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
