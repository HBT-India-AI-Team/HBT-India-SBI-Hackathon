# FinGuru — Dynamic Financial Tools: Front End Spec

**Scope:** Prototype/demo. Tools (EMI calculator, savings tracker, etc.) are
NOT hardcoded components. The front end is a generic interpreter that
renders UI and runs calculations purely from a JSON schema sent by the
backend, so adding a new tool never requires a front end code change.

---

## 1. Core Principle

There is exactly ONE generic tool-renderer component. It does not know in
advance what tools exist. Everything about a tool — its name, its inputs,
how to render them, and how to compute its result — comes from a JSON
definition served by the backend at runtime.

---

## 2. Tool Definition Schema (received from backend)

```json
{
  "tool_id": "emi_calculator",
  "name": "EMI Calculator",
  "execution": "client",           // "client" or "server"
  "inputs": [
    { "key": "principal", "label": "Loan Amount", "type": "number" },
    { "key": "rate", "label": "Interest Rate (%)", "type": "number" },
    { "key": "tenure_months", "label": "Tenure (months)", "type": "number" }
  ],
  "formula": "principal * (rate/1200) * pow(1+(rate/1200), tenure_months) / (pow(1+(rate/1200), tenure_months) - 1)",
  "output_label": "Monthly EMI"
}
```

For `execution: "server"` tools, `formula` is omitted/ignored and results
come from a backend call instead (see §4).

---

## 3. Rendering

- On chat load / tools-tab load, fetch the list of available tool
  definitions from the backend (`GET /api/tools`).
- For each tool triggered in chat or opened in the tools tab, render inputs
  purely from the `inputs` array — map `type` to a standard input component
  (number → numeric input, dropdown → select, etc.). Do not special-case any
  `tool_id` in rendering logic.
- When the user submits, branch on `execution`:
  - `"client"` → evaluate `formula` locally (see §4).
  - `"server"` → POST inputs to the backend (see §5).
- Display the result using `output_label`.

---

## 4. Client-Side Formula Execution — SECURITY REQUIREMENT

**Never use raw `eval()` or `Function()` on the formula string.** A formula
coming from a database, even an "internal, vetted" one, should still be
treated as untrusted input reaching the browser.

Use a proper math expression parser library (e.g. `mathjs`'s `evaluate()`,
or `expr-eval`) that only supports arithmetic/math functions and has no
access to arbitrary JS execution. Pass the user's input values as a scope
object to the evaluator rather than string-interpolating them into the
formula.

Example (using mathjs):
```js
import { evaluate } from 'mathjs';
const result = evaluate(tool.formula, inputValues); // inputValues = {principal, rate, tenure_months}
```

Scope this to arithmetic-only tools. If a tool needs conditionals, loops, or
multi-step logic, it should be marked `execution: "server"` — do not try to
stretch the expression parser to cover that.

---

## 5. Server-Side Execution Path

For tools marked `execution: "server"`:
```
POST /api/tools/execute
{
  "tool_id": "complex_tool_id",
  "inputs": { "field1": 123, "field2": "value" }
}

Response:
{ "result": ..., "output_label": "..." }
```
Front end just packages whatever the user filled into the generic `inputs`
schema and posts it — no tool-specific logic needed here either, since the
backend owns the actual computation.

---

## 6. Tools Tab vs. Inline Chat Result

- Chat-triggered tool use renders an inline compact card (inputs + result +
  an "open in Tools tab" / "save" action).
- Saving persists the tool's current input values against the user's `name`
  (see backend spec) so it reappears in the dedicated Tools tab across
  sessions.
- Tools tab lists all of a user's saved tool instances, each re-rendered via
  the same generic renderer.

---

## 7. New Tool Rollout (no front end deploy needed)

When the backend adds a new row to the tools table, the front end picks it
up automatically the next time it calls `GET /api/tools` — no code change,
no redeploy. This should be verified as part of the demo: add a 3rd tool via
DB entry only, confirm it appears and works without touching front end code.
