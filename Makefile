.PHONY: install build test lint fmt clean

install:        ## editable install with dev extras
	uv pip install -e ".[dev]"

build:          ## sdist + wheel into dist/
	uv build

test:           ## run the test suite
	.venv/bin/pytest tests/ -q

lint:           ## ruff check
	.venv/bin/ruff check agentdeck/

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
