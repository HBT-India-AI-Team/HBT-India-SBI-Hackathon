# Test Harness — Scenario Index

Human-readable Given/When/Then acceptance scenarios. Each file documents the
scenario, what's expected, and a "Verified" note pointing at the real run that
confirmed it (curl walk and/or UI screenshot via Playwright — see
`docs/BUILD_LOG.md` for the full evidence).

> Note: this `test-harness/` folder existed only as an empty `fixtures/`
> directory before this pass (no prior scenario files or RUN_ALL index to
> extend — see the FinGuru build's Phase 9 log entry in `docs/BUILD_LOG.md`).
> This file is created fresh, currently covering the FinGuru feature only.
> Add onboarding-flow scenarios here in the same format if/when they're written.

## FinGuru (financial Q&A assistant)

1. [finguru_grounded_answer.md](finguru_grounded_answer.md) — asking a
   well-covered question returns a cited, grounded answer with follow-ups and
   a tappable glossary term.
2. [finguru_gap_filling_loop.md](finguru_gap_filling_loop.md) — an uncovered
   question → opt-in to research → admin resolves via the shared HITL queue →
   notification → answer-ready banner → resumed chat with the researched
   answer.
3. [finguru_comparison_mode.md](finguru_comparison_mode.md) — the explicit
   "compare with generic AI" toggle produces two genuinely different answers
   (grounded+cited vs. ungrounded+uncited) for the same question.
4. [finguru_onboarding_handoff.md](finguru_onboarding_handoff.md) — a
   product-related FinGuru answer surfaces a real handoff card that starts a
   genuine onboarding `Application` via the same `/applications/start`
   endpoint the rest of the app uses.
