import type {
  AddSkillResult,
  AgentFiles,
  AgentRouterResult,
  AgentSummary,
  ApiKeyResult,
  Capability,
  ChatTurnResult,
  InputMode,
  GenerateAgentEvent,
  GenerateAgentResult,
  OllamaCallLog,
  RefineAgentResult,
  SaveResult,
  SkillCatalogEntry,
  TemplateSummary,
  TestRunResult,
} from './types'

export class ApiRequestError extends Error {}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiRequestError(body.detail ?? `Request to ${path} failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  listAgents: () => apiFetch<{ agents: AgentSummary[] }>('/admin/agents'),
  listStages: () => apiFetch<{ stages: string[] }>('/admin/stages'),
  listCapabilities: () => apiFetch<{ capabilities: Capability[] }>('/admin/capabilities'),
  listTemplates: () => apiFetch<{ templates: TemplateSummary[] }>('/admin/templates'),
  getOllamaLogs: (limit = 100) => apiFetch<{ calls: OllamaCallLog[] }>(`/admin/ollama-logs?limit=${limit}`),

  getAgentFiles: (agentId: string) => apiFetch<AgentFiles>(`/admin/agents/${agentId}/files`),

  saveAgentFiles: (agentId: string, files: Omit<AgentFiles, 'agent_id'>) =>
    apiFetch<SaveResult>(`/admin/agents/${agentId}/files`, {
      method: 'PUT',
      body: JSON.stringify(files),
    }),

  listSkillCatalog: () => apiFetch<{ skills: SkillCatalogEntry[] }>('/admin/skills'),

  addSkill: (
    agentId: string,
    payload: {
      skill_id: string
      mode: 'scaffold' | 'attach_existing'
      has_rules?: boolean
      template_id?: string
      description?: string
      purpose?: string
    },
  ) =>
    apiFetch<AddSkillResult>(`/admin/agents/${agentId}/skills`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  removeSkill: (agentId: string, skillId: string) =>
    apiFetch<AddSkillResult>(`/admin/agents/${agentId}/skills/${skillId}`, { method: 'DELETE' }),

  createAgent: (agentId: string, skillId: string, purpose: string, templateId: string) =>
    apiFetch<{ status: string; agent_id: string; skill_id: string; template_id: string }>('/admin/agents', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, skill_id: skillId, purpose, template_id: templateId }),
    }),

  getApiKey: (agentId: string) => apiFetch<ApiKeyResult>(`/admin/agents/${agentId}/api-key`),

  regenerateApiKey: (agentId: string) =>
    apiFetch<ApiKeyResult>(`/admin/agents/${agentId}/api-key/regenerate`, { method: 'POST' }),

  deleteAgent: (agentId: string) =>
    apiFetch<{ status: string; agent_id: string }>(`/admin/agents/${agentId}`, { method: 'DELETE' }),

  generateAgent: (agentId: string, purpose: string) =>
    apiFetch<GenerateAgentResult>('/admin/agents/generate', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, purpose }),
    }),

  // Streams step-by-step progress over SSE (decompose, each skill's rule
  // generation, save, validate) instead of one opaque call. Calls onEvent
  // as each step arrives and resolves with the final result.
  generateAgentStream: async (
    agentId: string,
    purpose: string,
    onEvent: (event: GenerateAgentEvent) => void,
  ): Promise<GenerateAgentResult> => {
    const response = await fetch('/admin/agents/generate/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, purpose }),
    })
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({ detail: response.statusText }))
      throw new ApiRequestError(body.detail ?? `Request to generate agent failed (${response.status})`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let finalResult: GenerateAgentResult | null = null

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() ?? ''
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        const event = JSON.parse(line.slice('data: '.length)) as GenerateAgentEvent
        onEvent(event)
        if (event.step === 'final' && event.result) finalResult = event.result
      }
    }

    if (!finalResult) throw new ApiRequestError('Agent generation stream ended without a result')
    return finalResult
  },

  refineAgent: (agentId: string, feedback: string) =>
    apiFetch<RefineAgentResult>(`/admin/agents/${agentId}/refine`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    }),

  setInputMode: (agentId: string, inputMode: InputMode) =>
    apiFetch<{ status: string; agent_id: string; input_mode?: InputMode; error?: string }>(
      `/admin/agents/${agentId}/input-mode`,
      { method: 'POST', body: JSON.stringify({ input_mode: inputMode }) },
    ),

  chatWithAgent: (agentId: string, sessionId: string | null, message: string) =>
    apiFetch<ChatTurnResult>(`/admin/agents/${agentId}/chat`, {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, message }),
    }),

  testRunAgent: (agentId: string, input: Record<string, unknown>) =>
    apiFetch<TestRunResult>(`/admin/agents/${agentId}/test-run`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    }),

  runAgentRouter: (input: Record<string, unknown>) =>
    apiFetch<AgentRouterResult>('/workflows/agent_router/invoke', {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  renderExplanationMarkdown: (explanation: Record<string, unknown>) =>
    apiFetch<{ markdown: string }>('/admin/explain/markdown', {
      method: 'POST',
      body: JSON.stringify({ explanation }),
    }),
}
