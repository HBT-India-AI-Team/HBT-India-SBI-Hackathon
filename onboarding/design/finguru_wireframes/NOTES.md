# FinGuru wireframes — design NOTES

Reference for every FinGuru UI phase (3–9). Colors here are the authoritative
tokens from `finguru_extension/DESIGN.md` (the Stitch export shipped with the
wireframes), cross-checked by eye against the actual `screen.png` images in
this folder. **Reference this file in every FinGuru UI phase so styling stays
matched to the wireframes, not re-guessed.**

## Palette (exact tokens from finguru_extension/DESIGN.md)

| Role | Hex | Usage in the wireframes |
|---|---|---|
| **Primary — SBI Blue** | `#00386B` | Wordmark, headlines ("How can I guide you today?"), user chat bubbles (filled blue, white text), send button, primary pill buttons, bottom-nav selected pill |
| Primary light / container | `#1A4F8A` | Blue robot mascot circle, secondary blue accents |
| on-primary | `#FFFFFF` | Text on blue |
| **Secondary — FinGuru Gold (bright)** | `#F6BE39` | Mascot circle fill (gold owl/lightbulb), mic button on ask bar, SIP slider thumbs, "Est. Returns" dot |
| Secondary container (light gold) | `#FFC641` | Lighter gold fills |
| Secondary (deep gold, text) | `#795900` | "FINGURU" label text on gap card, gold body accents |
| on-secondary-container (gold text) | `#715300` | Follow-up chip text, "Learn more" links, glossary term text, citation-pill wisdom text |
| Cream / advisory tint | `#FBF6E8`–`#FDF8EC` | Background of "not covered" gap card & "Researched for you" answer header |
| Peach notification | bg `#FCE3C6`, border/text gold `#B26B00` | Home "FinGuru found an answer" banner |
| Error / fraud red | `#BA1A1A`, container `#FFDAD6`/`#FFE9E6` | Fraud-awareness card (red border, pink bg, red text, "Report User" filled-red button) |
| Success green | `#1E7A34` | positive trend accents (reused from onboarding) |
| Canvas / chat bg | `#F2F4F7` (DESIGN.md) / `#FBF9F8` surface | Chat stage behind cards |
| Card surface | `#FFFFFF` | Answer cards, tiles, scheme cards |

Shadows: soft, blue-tinted ambient `0 4px 20px rgba(26,79,138,0.08)` — never harsh black.

## Shape & type language

- **Font:** Quicksand throughout (already used app-wide via `google_fonts`).
- **Radii:** cards/containers 16–24px; main action buttons fully pill (9999px);
  chips pill with 1px border; inputs 12px.
- **Mascot:** a small owl / lightbulb-robot. Rendered in a **gold circle** beside
  bot answer bubbles (some screens show it in a blue circle — gold is the
  FinGuru-advisory default; blue is used for the plain assistant robot). We use a
  gold circle with a lightbulb/owl glyph for FinGuru answer bubbles.
- **Bottom nav** (5 tabs): Home · Game · **FinGuru** (selected = blue pill) ·
  Accounts · Profile. The existing onboarding app has no global bottom nav; the
  FinGuru screens carry their own header + this nav to match the wireframes.

## Chat conventions

- **User bubble:** filled SBI-blue, white text, right-aligned, asymmetric rounding.
- **Bot answer:** white card, left-aligned, gold mascot circle to its left, a small
  **speaker (read-aloud) icon** top-right.
- **Citation pill:** gray pill "🔖 Per RBI guidelines"-style tag under the answer,
  with a right-aligned "Last verified: <month year>" label when the source topic
  has `last_verified_at`.
- **Follow-up chips:** **outlined-gold** pills with gold text (distinct from the
  onboarding chat's *filled* blue quick-reply chips). Tapping sends it as the next
  question.

## Screen-by-screen map (folder → screen)

| Folder | Screen / purpose | Phase |
|---|---|---|
| `finguru_home/` | **FinGuru home** — gold lightbulb + "FinGuru" wordmark, "How can I guide you today?", pill ask-bar (search + gold mic), "Trending this week" horizontal cards, "Explore Knowledge" 3 tiles (Fin Wiki=gold accent, Products=blue accent, Govt Schemes=gold accent, each a circular icon + left accent bar), "What others are asking" list with chevrons | 3, 5 |
| `finguru_chat_citation_view/` | **Chat answer w/ citation** — blue user bubble, white answer card + gold mascot + speaker icon, bold figures, divider, gray citation pill "Per RBI guidelines" + "Last verified: Jul 2026", outlined-gold follow-up chips ("How to apply?", "Compare with SIP"), "Ask FinGuru…" input + mic + blue send | 3 |
| `finguru_glossary_tooltip/` | **Tap-to-define popover** — term in answer shown gold + dotted underline; white popover "NAV (Net Asset Value) / short definition / Learn more →" (gold link) | 3 |
| `finguru_info_not_found/` | **Gap: not covered** — cream card, gold "FINGURU" label, "I don't have solid info on this yet — want me to look into it…?", **gold filled pill "Yes, research this"** + gold text link "No thanks" | 4 |
| `finguru_research_queued/` | **Research queued** — white card, hourglass icon, "Got it — I'll dig into this and notify you…", divider + clock "Usually ready within a day" | 4 |
| `finguru_home_answer_ready_notification/` | **Answer-ready banner** — peach banner on home: gold lightbulb + "FinGuru found an answer to your question about <topic>" + arrow (gold-accented, mirrors onboarding resume banner) | 4 |
| `finguru_researched_answer_result/` | **Researched answer** — answer card with a cream **"💡 Researched for you"** gold header tag on the bubble; follow-up chips below | 4 |
| `finguru_sip_calculator/` | **SIP calculator card** — embedded chat card "🧮 SIP Calculator", 3 gold-thumb sliders (Monthly investment ₹, Expected return %, Time period Yr) with gold value labels, "Estimated Value ₹X" (blue), invested-vs-returns split bar (blue+gold) with legend, blue pill "Invest Now →" | 6 |
| `government_schemes_explorer/` | **Schemes explorer** — "Government Schemes" header, filter chip row (All selected=blue pill, Savings/Insurance/Pension/…), white scheme cards: bold blue title, one-line summary, eligibility tag chips, expand chevron | 6 |
| `finguru_comparison_mode/` | **Comparison mode** — "FinGuru Chat / Live Comparison Mode", one big blue panel containing two stacked cards: top = **FinGuru** (gold mascot + gold "✓ Cited & India-specific" badge, answer with `[1]`/`[2]` superscript refs + Sources list); bottom = **Generic AI** (gray robot, italic answer, "No specific sources cited"); white "Ask another question" pill | 7 |
| `finguru_disclaimer_footer/` | **Disclaimer footer** — persistent gray bar above input: "ⓘ FinGuru gives educational information, not personalized investment advice. **Learn more**" (gold link); also shows answers with structured sub-cards (Trading/Management, each icon + text) | 8 |
| `finguru_fraud_awareness_alert/` | **Fraud warning** — red-bordered pink card, ⚠️ "Heads up — schemes promising 'guaranteed high returns' are a common fraud pattern. Here's what to watch for:", red bullets (Unrealistic return rates, Pressure to act quickly, Vague company details), buttons "Report User" (red filled) + "Learn More" (red outline) | 8 |
| `finguru_onboarding_handoff/` | **Onboarding handoff card** — bot text + card "🚀 Ready to start a SIP?", body, gray inset "🛡 Secure Handoff to State Bank of India", **gradient (gold→blue) pill "Get Started with SBI →"**, owl mascot | 8 |
| `kinetic_heritage/` | The base onboarding design system (already implemented) — kept for palette parity. | — |

## Notes / deviations

- Only `finguru_extension/` and `kinetic_heritage/` ship a `DESIGN.md`; the gold
  tokens above come from `finguru_extension/DESIGN.md`.
- Some wireframe headers say "FinGuru" and others "YONO 3.0 / FinGuru Assistant" or
  "Chat" in the bottom nav — we standardize the tab label to **FinGuru** and the
  in-screen header to the FinGuru wordmark.
- The bottom-nav's Accounts/Profile/Game tabs are shown for visual fidelity but only
  Home, Game (existing placeholder), and FinGuru route to real screens in this build.
</content>
