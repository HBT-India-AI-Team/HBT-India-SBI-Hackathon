# fin_health — scoring rules, for the finance team

This explains how fin_health decides Accepted / Review / Rejected, and how
to change that yourselves without needing a developer.

## The short version

The score is **pure arithmetic on the rule files in `rules/`** — spreadsheet
math, not an AI guess. Whatever model powers the agent's write-up, the score
and the Accepted/Review/Rejected outcome never change; only the wording of
the explanation does. You can safely edit the rule files below and trust
that the number changes exactly the way you'd expect.

## The four rule files, in the order they run

1. **`rules/gates.yaml`** — hard pass/fail checks. If the company fails any
   gate (e.g. its MCA status isn't Active), it's rejected immediately and
   nothing below even runs.
2. **`rules/factors.yaml`** — the actual scoring. Every real financial
   figure gets converted to a 0-100 score using "bands" (see the comments
   inside that file for exactly how bands work), grouped into categories
   like Profitability and Leverage.
3. **`rules/composite.yaml`** — combines the categories into one final
   0-100 score using weights, then applies the Accepted/Review/Rejected
   thresholds. **This is the file to edit if you just want to change how
   strict the cutoffs are.**
4. **`rules/product_fit.yaml`** — which track an eligible company gets
   routed to (today, always the tender proposal track).

Each file has detailed comments at the top explaining exactly how to add a
new rule to it — start there.

## Every real field you can write a rule against

These come from parsing the MCA due-diligence Excel report
(`backend/excel_ingest.py`) — every one is a real number/value read
directly off the report, never estimated:

| Field | Type | Meaning |
|---|---|---|
| `company_status` | text | MCA company status (e.g. "Active") |
| `gst_status` | text | GST registration status (e.g. "Active") |
| `ebitda_margin_pct` | number | Latest-year EBITDA margin, percent |
| `net_margin_pct` | number | Latest-year net margin, percent |
| `revenue_growth_pct` | number | Latest-year revenue growth, percent |
| `debt_to_equity` | number | Latest-year debt-to-equity ratio |
| `interest_coverage_ratio` | number | Latest-year interest coverage ratio |
| `current_ratio` | number | Latest-year current ratio |
| `epfo_late_payment_flag` | true/false | EPFO shows a late-payment flag in the last 12 months |
| `charges_to_capital_ratio` | number | Total secured charges ÷ paid-up capital |
| `paid_up_capital_cr` | number | Paid-up capital, ₹ crore (available, not currently scored) |
| `total_secured_charges_cr` | number | Total secured charges, ₹ crore (available, not currently scored) |
| `business_name` | text | Company legal name (for display only, never scored) |

Want to score on something not in this list (e.g. a different ratio from
the report)? That needs one small code change in
`backend/excel_ingest.py` to actually extract it from the Excel sheets —
ask a developer for that one step, then the new field works in the rule
files exactly like these do.

## How to make a change yourself

Open the relevant file under **Agent Editor → fin_health → rules/**, or ask
to have it opened directly, and edit the numbers. A few common changes:

- **"Companies need to hit a higher bar to be Accepted"** → raise
  `qualified_min` in `rules/composite.yaml`.
- **"Debt matters more than growth to us"** → raise
  `Leverage and Liquidity`'s weight and lower `Growth`'s weight in
  `rules/composite.yaml` (they don't need to sum to 1, but it's easier to
  read if they do).
- **"A company with X should score higher/lower for that"** → find the
  relevant factor in `rules/factors.yaml` and adjust its `bands` (see the
  comment at the top of that file for exactly how bands work).
- **"We should also hard-reject companies with Y"** → add a new gate to
  `rules/gates.yaml`.

After saving, test it immediately in **Playground → fin_health → File
mode**, either with a real report or the "Fill sample data" button (uses
the real Japtech sample this was built against) — you'll see the new score
right away, plus a step-by-step trace of what ran.

## One caveat

If someone ever uses "Fix with AI" on this skill (the natural-language
rule editor), it regenerates these four YAML files from scratch and will
**overwrite the comments in them** (though your actual rule *values* will
be preserved/updated per whatever you asked for). This README itself is
never touched by that, so the explanations here stay put even if the YAML
files' comments don't.
