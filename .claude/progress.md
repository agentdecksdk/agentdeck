# Execution Model Audit (v6.0.3) -- COMPLETE

Worktree: wrts/audit-exec-603, branch audit/execution-model-v6.0.3, base b9a7f76 (v6.0.3)
Read-only audit. No production code changed (`git status`: only `analysis/` and this file).
Baseline gate: `make check` green (ruff, ty, import-linter 13 contracts, 1876 passed / 188 skipped).

## Phases
- [x] 0. Worktree + orientation
- [x] 1. Execution mental model (real map, traced not documented)
- [x] 2. Behavioral contract (4 real paths, not 4 "modes")
- [x] 3. Cancellation
- [x] 4. Shutdown
- [x] 5. Async
- [x] 6. Sync
- [x] 7. Thread
- [x] 8. Process -- NONE EXISTS, documented as a scoping fact
- [x] 9. Context
- [x] 10. Reporter
- [x] 11. Exceptions
- [x] 12. Run state machine
- [x] 13. Resource ownership
- [x] 14. Test coverage
- [x] 15. Simplicity

## Outputs
- analysis/EXECUTION_MODEL_AUDIT.md (15 sections + appendix, 21 findings EXEC-01..EXEC-21)
- analysis/EXECUTION_STATE_MACHINE.md
- analysis/EXECUTION_TEST_MATRIX.md

## Verdict
One coherent event/state/store model with two backends, plus a leaky cooperative-control seam.
`Run.cancel()` means three different things depending on what is executing; two of the three are
silent no-ops for the code a user writes by default.

## Reproduced defects (probes in the session scratchpad, not committed)
EXEC-01 (safepoint in an agent tool -> tool error), EXEC-02 (cancel no-op for async native),
EXEC-03 (can.pause lies, signal stranded), EXEC-04 (unbounded aclose), EXEC-05 (_parked leak),
EXEC-06 (_tree leak on SessionBusyError), EXEC-08 (run vs stream), EXEC-09 (parallel sibling
survives), EXEC-11 (FAILED not sealed), EXEC-15 (poll-window swallow).
