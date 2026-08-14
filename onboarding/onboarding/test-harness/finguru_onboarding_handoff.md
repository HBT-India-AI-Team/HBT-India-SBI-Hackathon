# Scenario: FinGuru → onboarding handoff

**Given** the FinGuru knowledge base includes product topics tagged
`product_id:<id>` matching real entries in
`backend/data/product_requirements.json` (loaded via `product_catalog.py`)

**When** a user asks a product-related question, e.g. "What SBI savings
account can I open online?" (`POST /finguru/conversations/{id}/message`)

**Then** the response is grounded and cites the matching product topic (e.g.
`product_savings_account`)
**And** `suggested_action: "start_onboarding"` with `suggested_product_id` set
to a product id that is validated against the SAME
`product_catalog.get_product()` onboarding uses (not duplicated product data;
an invalid/stale tag would be silently dropped, never surfaced)
**And** the Flutter chat renders the gradient gold→blue "Get Started with SBI"
handoff card below the answer, matching the wireframe (rocket emoji header,
"Secure handoff to State Bank of India" inset, gradient pill button)

**When** the user taps "Get Started with SBI"

**Then** the app calls the exact SAME `POST /applications/start` endpoint the
rest of onboarding uses, with `source: "finguru"` and the suggested
`product_id`
**And** a real `Application` row is created (`GET /admin/applications` shows
it with `status: IN_PROGRESS`)
**And** the user is navigated into the real onboarding chat (`/chat`), which
starts the exact same Requirement Graph flow as onboarding entered from the
home screen

## Verified
2026-08-12 (docs/BUILD_LOG.md Phase 8): curl confirmed
`suggested_action:"start_onboarding"`, `suggested_product_id:"savings_account"`.
UI screenshot via Playwright: the handoff card rendered per the wireframe;
tapping "Get Started with SBI" navigated into the real "YONO Assistant"
onboarding chat with its progress stepper and first prompt. Confirmed via
`GET /admin/applications` that a genuine new `Application`
(`product_id:savings_account`, `status:IN_PROGRESS`) was created — a real
handoff, not a mock transition.
