# Design

<!-- impeccable:design-schema 1 -->

## Direction

**Restrained** color strategy on a light ground, Operate mode. Redesigned from an all-dark, indigo-accent theme at the user's explicit request, using two reference screenshots of a comparable admin panel (persistent sidebar, top bar + table lists, breadcrumb + split-pane markdown editor) as structural inspiration and a navy/gold source palette. No product name or logo mark is shown anywhere in the UI (explicit constraint) — the sidebar carries a plain generic lightning-bolt glyph, not a wordmark tied to any brand.

## Palette

Two custom scales added to `src/index.css` via Tailwind v4 `@theme`, alongside Tailwind's stock `neutral`, `emerald`, `amber`, `red` for semantic status:

- `brand-50…950` — navy (`brand-600 #1f4f8c` is the primary interactive color: buttons, links, selected states, focus rings).
- `gold-50…900` — warm gold (`gold-400 #f8b524`), used only as a single sparing accent: the active-nav indicator bar in the sidebar. Never a second primary; never used for buttons or large fills.
- Semantic status (success/warning/danger) stays emerald/amber/red at `-50/-200/-700` tint levels on light surfaces — unchanged in meaning from the previous dark theme, just recontrasted for a white ground.
- Page background `neutral-50`, card/surface `white`, borders `neutral-200`, body text `neutral-900` / secondary `neutral-500`.

## Layout

- Persistent left sidebar (`brand-900` navy, fixed width `w-56`): two nav items only — Agents, Playground — no account/org/settings chrome (explicit constraint; this is a single-purpose internal tool, not multi-tenant SaaS).
- Main content: white top bar (page title + primary action) over a `neutral-50` scroll area, or — inside an open agent — a breadcrumb bar, tab row, and a two-pane files view (skill/file tree left, editor/preview right).
- Agent list is a **table**, not the old card grid: Name (+ status dot) / Description (+ inline badges) / row-hover delete — closer scanability at higher agent counts, matching the reference's list pattern.

## Components (`src/components/ui.tsx`)

Shared primitives carried over from the prior polish pass, recolored: `Button` (primary/success/danger/secondary/ghost), `ChoiceCard`, `SegmentedControl`, `Modal`, `TextInput`/`TextArea`, `TrashIcon`/`DangerIconButton`, `Skeleton`. Added in this pass: `Badge` (tone: brand/gold/success/warning/danger/neutral) and `StatusDot`, replacing ~6 hand-rolled pill/dot implementations that had drifted across files.

## Typography

System sans throughout (unchanged) — Operate mode, one family, fixed rem scale. No display face introduced.

## What to preserve going forward

- Sidebar stays exactly two items unless a genuinely new top-level surface is added — do not grow it into a generic nav dumping ground.
- Gold stays a one-spot accent. Reaching for it on a second element is drift, not intent.
- No badge, pill, or status dot gets hand-rolled again — extend `Badge`/`StatusDot` instead.
