# P5: Divination provider_fn Real Wiring

## Goal

Replace `DivinationEnrichment(provider_fn=None)` in production with a real `provider_fn`
that calls the `divination_consumer.DivinationClient.analyze(domain, payload)` against the
Provider (divination-knowledge-engine). Enrichment must stay a non-blocking side note:
any failure degrades to empty annotation and never blocks the conversation or safety flow.

## Context (verified facts)

- Provider `/api/{domain}/analyze` (liuyao/ziwei/bazi) expects **structured divination facts**
  (e.g. liuyao requires `month_zh`, `day_zh`, `yong_shen`). It does NOT accept free text;
  POSTing `{"text": text}` returns 422 (missing required fields). Response shape is
  `{domain, matched_rules, conclusions, total_weight, matched_count}` — **no `symbols` field**.
- Consequence: a free-text `enrich(text)` call against the real Provider yields either a
  422 (`DivinationAPIError`) or a 200 whose body has no `symbols` → `_extract_symbols` returns
  empty → degraded. Both outcomes are safe and accepted. P5 does NOT build a facts-extractor
  from chat text; it only wires the transport so the real branch executes.
- Provider startup: `uvicorn main:app` with `cwd=<repo>/engine` (contains `main.py`, `rule_engine.py`).
- `divination_consumer.DivinationClient()` constructor only reads local vendor manifest/schema
  files (no network); `analyze()` is the only network call.

## Requirements

1. Extend `provider_fn` type from `Callable[[str], Mapping]` to `Callable[[str, str], Mapping]`
   and pass `text` through: `self._provider_fn(self._domain, text)`.
2. New module `backend/divination/provider_factory.py`:
   - `build_provider_fn() -> Callable[[str, str], Mapping] | None`.
   - Reads `XINJING_ENGINE_PATH`; if unset / missing / import fails → return None (log warning).
   - `sys.path`-based import of `divination_consumer.client.DivinationClient` (no pip install).
   - Returned closure lazily constructs the client (cached) and calls
     `client.analyze(domain, {"text": text})`; propagates exceptions to enrich().
   - No network / no client construction at module import time.
3. Wire `chat.py` L21 to `DivinationEnrichment(provider_fn=build_provider_fn())`.
4. Do NOT touch P4 safety boundaries: crisis early-return, SAFE_DEGRADED gate,
   SAFETY_PLAN exclusion, `system_parts`-only annotation flow, try/except fallback.
5. New unit tests `tests/test_p5_provider_factory.py` (8 tests, MagicMock + monkeypatch, no real HTTP).
6. New CI job `test-real-divination-link`: checkout heartmirror + engine(pin) + provider(public),
   install deps, set env, run `tests/test_p5_real_divination_link.py` which boots a real
   uvicorn Provider subprocess (no skip; startup failure → CI fail).

## Hard Constraints (P4 invariants, re-verified)

- `enrich()` never raises; all exceptions → degraded.
- enrichment only runs in non-CRISIS, non-SAFETY_PLAN, non-SAFE_DEGRADED branch.
- annotation only reaches `system_parts`, never `arb_result`.
- Provider unavailable → empty side note, conversation not blocked.
- No pip-install of the engine package; sys.path on-demand import only.
- manifest content-fingerprint gate is fail-closed by design; do not bypass.

## Acceptance Criteria

- [ ] provider_fn signature `(domain, text)`; `enrich()` passes `text`.
- [ ] `provider_factory.py` exists; `build_provider_fn()` returns None on import failure.
- [ ] `chat.py` uses `build_provider_fn()` instead of `provider_fn=None`.
- [ ] P4 safety boundaries unchanged (diff confirms chat.py safety logic intact).
- [ ] All new P5 unit tests pass.
- [ ] Full regression: baseline 102 passed + 6 skipped preserved, total increased.
- [ ] CI `test-real-divination-link` job boots real Provider uvicorn; no skip.
- [ ] Trellis task finished + archived; heartmirror pushed to origin main.
