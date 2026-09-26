# P4 Divination Chat Integration

## Goal

Wire DivinationEnrichment as optional LLM context augmentation into chat.py non-crisis branch; add 5 tests; no changes to P1/P2/P3 safety logic.

## Requirements

1. Module-level singleton `_divination_enrichment = DivinationEnrichment(provider_fn=None)` in `backend/routers/chat.py` (safe default: degraded, no side notes).
2. In the non-crisis, non-SAFETY_PLAN flow (after line 109, before `system_parts` build), call `enrich(body.message)` with try/except fallback; only render when `is_usable`.
3. Append rendered annotation to `system_parts` under a clearly-marked optional prefix; never touch `arb_result`.
4. Crisis branch, SAFE_DEGRADED branch, `arbitrate`, `check_safe_boundary`, SafetyInput flow untouched.
5. New test file `tests/test_p4_divination_chat.py` with 5 tests covering: crisis skips enrichment, non-crisis adds annotation, enrichment failure non-blocking, degraded result skipped, enrichment data not in arb_input.

## Hard Constraints

- enrichment runs ONLY after crisis + SAFE_DEGRADED early returns; `target_level in ("CRISIS", "SAFETY_PLAN")` explicitly skipped.
- `enrich()` call wrapped in try/except; any exception logs warning and does not block.
- Annotation only appended when `div_result.is_usable` is True.
- Annotation text only reaches `system_prompt`; never `arb_result` or safety scoring.
- Do NOT rename `from ..engine_client import arbitrate` (P0-B tests monkeypatch `backend.routers.chat.arbitrate`).

## Acceptance Criteria

- [ ] `python -m pytest tests/ -q` → 107+ passed, no regressions.
- [ ] `python -m pytest tests/test_security_hardening.py -q` → all P0-B fail-closed tests pass.
- [ ] 5 new tests in `tests/test_p4_divination_chat.py` all pass.
- [ ] Diff stat ≈ +60~80 lines (chat.py integration + new test file).
- [ ] Commit on main, pushed to origin/main.
