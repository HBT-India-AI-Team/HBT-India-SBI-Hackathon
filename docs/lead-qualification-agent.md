# Lead Qualification Agent

Segment: SME / business banking. Config lives entirely in `skills_library/lead_qualification/` — this doc explains what that config actually does.

## Evidence shape

`gather_evidence` assembles one dict per run:

```
evidence = {
  "lead":       { business_name, segment, industry, business_vintage_years, requested_amount_cr },
  "financials": { annual_turnover_cr, turnover_growth_pct, current_ratio, dscr,
                  existing_obligations_cr, monthly_avg_balance_lakhs },
  "bureau":     { score, delinquencies_12m, default_flag },
  "kyc":        { status, sanctions_hit, gst_filing_regularity_pct },
}
```

Sourced from `capabilities_impl/lead_data.py`, `credit_bureau.py`, `kyc.py` (mock, JSON-fixture-backed).

## Hard gates (`rules/gates.yaml`)

Every gate is evaluated (none short-circuit, so the explanation always shows the full picture). If multiple fail, the **most severe** wins — `NOT_QUALIFIED` beats `NEEDS_HUMAN_REVIEW`.

| gate | condition | on fail |
|---|---|---|
| `KYC_COMPLETE` | `kyc.status == VERIFIED` | NOT_QUALIFIED |
| `NO_SANCTIONS_HIT` | `kyc.sanctions_hit == false` | NOT_QUALIFIED |
| `MIN_VINTAGE` | `lead.business_vintage_years >= 1` | NOT_QUALIFIED |
| `NO_BUREAU_DEFAULT` | `bureau.default_flag == false` | NOT_QUALIFIED |
| `BUREAU_SCORE_FLOOR` | `bureau.score >= 650` | NEEDS_HUMAN_REVIEW (soft gate) |

## Weighted scoring (`rules/factors.yaml`)

Four categories, each a weighted average of banded factors (highest matching band wins):

- **financial_health** — turnover trend (0.4), current ratio (0.3), cash buffer (0.3)
- **credit_risk** — bureau score (0.5), DSCR (0.5)
- **business_risk** — vintage (0.4), GST filing regularity (0.6)
- **growth_potential** — turnover trend, more generous bands (single factor)

## Composite (`rules/composite.yaml`)

```
composite = 0.30 * financial_health + 0.35 * credit_risk + 0.20 * business_risk + 0.15 * growth_potential

>= 75  -> QUALIFIED
>= 55  -> CONDITIONALLY_QUALIFIED
<  55  -> NOT_QUALIFIED
```

A gate failure overrides this entirely — the composite is still computed and shown, but doesn't drive the decision.

## Product matching (`rules/product_fit.yaml`)

Each product declares `when` conditions (all must pass to match). Matches are ranked by how many conditions matched (more specific first), top 3 kept:

- **Working Capital Overdraft** — turnover growth ≥ 0 and current ratio ≥ 1.0
- **Term Loan (Business Expansion)** — turnover growth ≥ 15, DSCR ≥ 1.25, vintage ≥ 3
- **Trade Finance Facility** — industry in {manufacturing, trading, import_export}
- **Current Account Relationship** — fallback, always matches, ranked last

Skipped entirely if a gate already forced `NOT_QUALIFIED` — no point recommending products to a lead that's rejected.

## The LLM's job (and what it's not allowed to do)

`reason_llm` sends the evidence + gate/score/product results (never raw prompts with unnecessary data) to Ollama, constrained to `output_contract.json`'s schema: `summary`, `strengths[]`, `risks[]`, `next_best_action`, `product_rationale`, `confidence`. Every `strengths`/`risks` entry must cite an `evidence_key` — `output_validator.py` drops any point whose citation doesn't match a real fact from the prompt, and forces a retry if *every* citation is fake or required fields are missing/out-of-range.

The model is explicitly told (`instructions.md`) never to output a decision, and if it tries anyway, `output_validator.py` strips `decision`/`outcome`/`qualified`/`score` keys before they can reach anything downstream. The decision always comes from `decide`, never from the LLM.

## HITL escalation

`hitl_gate` checks `governance.hitl_conditions` (currently `low_confidence`, `validation_degraded`) against the LLM's self-reported confidence and whether validation had to fall back to the deterministic rationale. If triggered, a `QUALIFIED`/`CONDITIONALLY_QUALIFIED` outcome gets escalated to `NEEDS_HUMAN_REVIEW` — `NOT_QUALIFIED` is left alone (already terminal).

## Known limitation

The small model occasionally gets a narrative detail backwards in free text (e.g. `summary`, which isn't citation-checked the way `strengths`/`risks` are). Harmless for the decision itself — that's deterministic — but worth knowing if you're judging output quality. Swapping to a larger model in `agent.yaml`'s `llm.model` (the tunnel has several — see `docs/running.md`) improves this.
