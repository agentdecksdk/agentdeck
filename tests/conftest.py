"""Suite-wide pytest hooks that don't belong to any one test module."""


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    # `-q` (the Makefile's test target) prints nothing for passing tests, so the docs
    # executor's split between run and illustrative fences would otherwise be invisible.
    from test_docs_examples import ILLUSTRATIVE_CASES, RUN_CASES

    terminalreporter.write_line(f"docs examples: {len(RUN_CASES)} run, {len(ILLUSTRATIVE_CASES)} illustrative")
