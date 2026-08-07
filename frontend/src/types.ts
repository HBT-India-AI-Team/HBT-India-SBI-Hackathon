// Mirrors backend/admin.py and backend/main.py response shapes.

export interface InputSchemaProperty {
  type: 'string' | 'number' | 'integer' | 'object' | 'array'
  description?: string
}

export interface InputSchema {
  type?: string
  required?: string[]
  properties?: Record<string, InputSchemaProperty>
}

export interface ChatDecision {
  outcome: string
  reason: string
  composite_score: number | null
}

export interface ChatTurnResult {
  session_id: string
  reply: string
  evidence: Record<string, unknown>
  decision: ChatDecision | null
  done: boolean
}

export type InputMode = 'chat' | 'form' | 'json' | 'trigger'

export interface AgentSummary {
  agent_id: string
  version?: string
  purpose?: string
  skills?: string[]
  pipeline?: string[]
  routable?: boolean
  draft?: boolean
  input_schema?: InputSchema
  input_mode?: InputMode
  error?: string
}

export interface GeneratedSkillSummary {
  skill_id: string
  description: string
  used_fallback: boolean
}

export interface ApiKeyResult {
  agent_id: string
  api_key: string
}

export interface GenerateAgentResult {
  status: 'ok' | 'saved_with_errors' | 'error'
  agent_id: string
  skill_id?: string
  used_fallback?: boolean
  attempts?: number
  skills?: GeneratedSkillSummary[]
  error?: string
}

export interface GenerateAgentEvent {
  step: 'decompose' | 'generate_skill' | 'save' | 'validate' | 'final'
  status?: 'start' | 'done' | 'error'
  skill_id?: string
  index?: number
  total?: number
  count?: number
  used_fallback?: boolean
  error?: string
  result?: GenerateAgentResult
}

export interface RefineSkillResult {
  skill_id: string
  ok: boolean
  error: string | null
}

export interface RefineAgentResult {
  status: 'ok' | 'partial' | 'saved_with_errors'
  agent_id: string
  error?: string
  skills?: RefineSkillResult[]
}

export interface TemplateSummary {
  id: string
  label: string
  description: string
  pipeline: string[]
}

export interface SkillFiles {
  skill_yaml: string
  instructions_md: string
  task_prompt_md: string
  output_contract_json: string
  rules: Record<string, string>
}

export interface AgentFiles {
  agent_id: string
  agent_yaml: string
  skills: Record<string, SkillFiles>
}

export interface SkillCatalogEntry {
  skill_id: string
  kind: 'deterministic' | 'procedural'
  description: string
}

export interface AddSkillResult {
  status: 'ok' | 'saved_with_errors'
  agent_id: string
  skill_id: string
  error?: string
}

export interface SaveResult {
  status: 'ok' | 'saved_with_errors'
  error?: string
}

export interface Capability {
  name: string
  description: string
}

export interface TestRunResult {
  run_id: string
  decision: Record<string, unknown> | null
  explanation: Record<string, unknown> | null
  error: { stage: string; type: string; message: string } | null
}

export interface AgentRouterResult {
  workflow_id: string
  run_id: string
  status: 'COMPLETED' | 'NEEDS_CLARIFICATION' | 'AGENT_FAILED' | 'FAILED'
  chosen_agent_id: string | null
  routing_confidence: number | null
  routing_reasoning: string | null
  agent_run_id: string | null
  decision: Record<string, unknown> | null
  explanation: Record<string, unknown> | null
  error?: { stage: string; type: string; message: string }
}

export interface ApiError {
  detail: string
}
