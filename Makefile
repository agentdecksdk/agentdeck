.PHONY: install build test lint typecheck fmt clean check

install:        ## editable install with dev extras
	uv pip install -e ".[dev]"

build:          ## sdist + wheel into dist/
	uv build

test:           ## run the test suite
	.venv/bin/pytest tests/ -q

lint:           ## ruff check
	.venv/bin/ruff check agentdeck/ tests/

typecheck:      ## ty type check
	.venv/bin/ty check agentdeck

check: lint typecheck test   ## full gate

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
