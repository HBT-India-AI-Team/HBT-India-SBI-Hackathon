# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Internal engineers/ops building and testing banking decision agents (loan qualification, lead scoring, product recommendation). They work at a desk, iterating quickly between editing an agent's rules and running it against test input — not a public-facing audience.

## Product Purpose

A low-code editor for the agent platform: define an agent's pipeline, gates, scoring rules, and skills as YAML/JSON/Markdown files; test runs in a Playground; inspect the decision + explanation an agent produced. Success is fast, confident iteration on rule changes with immediate feedback, not a polished marketing surface.

## Positioning

Unlike hand-editing YAML in a code editor and re-deploying to see if it works, this tool lets someone edit an agent's rules and immediately run it against real or synthetic input in the same screen, seeing the structured decision/explanation output without leaving the tool.

## Operating Context

Agents are composed of skills (deterministic rule-sets or dynamic/procedural guidance). Editing happens file-by-file (agent.yaml, skill.yaml, instructions.md, rules/*.yaml, output_contract.json). A "draft" agent was AI-generated and needs human review before it's trusted/routable. The Playground can either target one agent directly or let an `agent_router` pick one from raw input.

## Capabilities and Constraints

- Existing capabilities: agent CRUD, skill attach/scaffold/remove, YAML raw+rendered preview, run agent (direct or auto-routed), decision/explanation viewer (cards + rendered markdown), reference panel of available pipeline stages/capabilities.
- No end-user auth/account UI in scope — this is an internal tool; no multi-tenant account switcher, billing, or org settings exist or are wanted.
- Backend is FastAPI, served same-origin in production; frontend is React 19 + Tailwind v4 + Vite.

## Brand Commitments

No company/product name should be surfaced in the UI chrome (explicitly requested — do not name or brand the tool). Visual identity is a navy (~#1f4f8c) + gold (~#f0a30c) palette on a light ground, inspired by a reference admin-panel screenshot (persistent left sidebar, top bar with page title + primary action, data tables for lists, breadcrumb + toolbar + split-pane editor for detail views). Dark theme is explicitly rejected.

## Evidence on Hand

Reference screenshots of a comparable admin panel (skills list table, split-pane markdown editor with live preview, orchestrator prompt editor) supplied by the user as structural/visual inspiration — not this product's actual content.

## Product Principles

1. Editing and running an agent happen in the same tool, side by side — never force a context switch to "see if it works."
2. Every destructive or hard-to-reverse action (delete agent, remove skill) gets an explicit confirm step; everything else should feel immediate.
3. A draft (AI-generated) agent is visually distinct until reviewed — never indistinguishable from a trusted one.
4. Density over decoration: this is a tool used many times a day by the same few people: reduce friction and scanning time before beauty.
5. No account/org/billing chrome — this is a focused internal tool, not a multi-tenant SaaS product.
