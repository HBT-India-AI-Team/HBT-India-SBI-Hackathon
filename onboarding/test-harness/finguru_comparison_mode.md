# Scenario: FinGuru live comparison mode

**Given** a FinGuru conversation with at least one user question already asked
**And** a reachable Ollama endpoint

**When** the user taps the balance-scale "Compare with generic AI" icon in the
chat header (an explicit opt-in, NOT the default per-question experience)
**And** the app calls `POST /finguru/compare` with that question

**Then** the response contains TWO genuinely different answers:
  - `finguru_answer`: the normal grounded `ask()` result, with `citations`
    when the question has coverage
  - `generic_answer`: a SEPARATE Ollama call using the SAME model but with
    NO retrieved-topic context and NO citation instruction (plain "answer
    this financial question" prompt) — never grounded, never cited
**And** the Flutter comparison screen renders a blue panel containing the
question, then a white "FinGuru" card (gold mascot, "✓ Cited & India-specific"
badge when citations exist, a Sources list) stacked above a gray "Generic AI"
card (italic text, "No specific sources cited")
**And** an "Ask another question" control re-runs the comparison for a new
question without leaving the screen

## Verified
2026-08-12 (docs/BUILD_LOG.md Phase 7): curl — "How does the Sukanya
Samriddhi Yojana work?" returned a grounded+cited `finguru_answer` and a
differently-worded, uncited `generic_answer` for the same question (confirming
they are genuinely two different calls, not two grounded calls). UI screenshot
via Playwright: tapped the compare icon, "Live Comparison Mode" screen showed
the FinGuru card with the gold "✓ Cited & India-specific" badge + a Sources
list, and the Generic AI card with unsourced italic text — visually and
substantively distinct.
