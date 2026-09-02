.PHONY: install build lock-check test lint typecheck lint-imports slop coverage golden docs-reference docs-impact roadmap-sync fmt clean check

# An agent reads this gate's output, so a passing step says nothing and a failing one says only
# what failed. 1,700 progress dots and 10 kept import contracts cost more attention than they
# carry, and pytest's default traceback buries the assertion under frames nobody reads.
# `make check V=1` restores every tool's own full output.
V ?= 0
# In-repo, not /tmp: two worktrees running `make check` at once each get their own tree, so the
# log can never interleave with another checkout's (#518).
LOG = $(CURDIR)/.make-$@.log
ifeq ($(V),0)
  E = @
  PYTEST_ARGS = -q --no-header --tb=line -rf
  QUIET = > $(LOG) 2>&1 && tail -2 $(LOG) | sed "/^[[:space:]]*$$/d; s/^/$@: /" \
          || { tail -40 $(LOG); echo "(full output: make $@ V=1)"; exit 1; }
else
  E =
  PYTEST_ARGS = -v --tb=short -ra
  QUIET =
endif

install:        ## editable install with every extra the gate needs, from the locked resolution
	# Every extra ci.yml installs, so `make check` locally runs the same tests CI does.
	# `.[dev]` alone silently skipped serve, postgres and observability. The whole
	# point of #142: a narrower install reads as a pass instead of as untested.
	# --locked: installs exactly what uv.lock resolved, and fails loudly if it's stale (#605)
	# instead of silently resolving fresh and leaving the lockfile to drift.
	uv sync --locked --all-extras

build:          ## sdist + wheel into dist/
	uv build

test:           ## run the test suite (includes the event-schema snapshot replay)
	$(E).venv/bin/pytest tests/ $(PYTEST_ARGS) $(QUIET)

# examples/ too: they are code a reader copies, and nothing else in the gate reads them.
lock-check:     ## uv.lock matches pyproject.toml -- the thing CI and `make install` actually install
	$(E)uv lock --check $(QUIET)

lint:           ## ruff check
	$(E).venv/bin/ruff check agentdeck/ tests/ examples/ $(QUIET)

typecheck:      ## ty type check
	$(E).venv/bin/ty check agentdeck $(QUIET)

lint-imports:   ## import-linter contracts (.importlinter, plus the fixture plugin's own)
	$(E).venv/bin/lint-imports $(QUIET)
	$(E).venv/bin/lint-imports --config tests/bindings/fixture_plugin/.importlinter $(QUIET)

slop:           ## anti-slop gate on lines this branch adds vs origin/dev
	$(E).venv/bin/python scripts/slopcheck.py --changed --base origin/dev < /dev/null $(QUIET)

coverage:       ## per-module coverage: audit input for #71/#131, not part of `make check`
	# Zero coverage is evidence a module *may* be dead, never proof: skill_runtime is
	# copied into sandbox venvs and the crossrun tests run out-of-process, so both read
	# as uncovered while being load-bearing. Corroborate with grep + the import graph.
	.venv/bin/pytest tests/ -q --cov=agentdeck --cov-report=term-missing:skip-covered

golden:         ## re-record the event-schema snapshots: deliberate, never automatic
	AGENTDECK_GOLDEN_UPDATE=1 .venv/bin/pytest tests/core -q

docs-reference: ## regenerate the five generated docs-site files from the code
	.venv/bin/python scripts/generate_docs_reference.py

docs-impact: ## report which documentation pages this branch's source changes affect
	$(E).venv/bin/python scripts/check_docs_impact.py --report

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
check: lock-check lint typecheck lint-imports test slop docs-impact   ## full gate

fmt:            ## ruff format + autofix
	.venv/bin/ruff format agentdeck/ && .venv/bin/ruff check --fix agentdeck/

clean:
	rm -rf dist/ build/ *.egg-info .ruff_cache .pytest_cache
