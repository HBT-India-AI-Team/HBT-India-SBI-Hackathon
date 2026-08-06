import { useEffect, useState } from 'react'
import { Dashboard } from './components/Dashboard'
import { AgentEditor } from './components/AgentEditor'
import { NewAgentModal } from './components/NewAgentModal'
import { ConfirmDialog } from './components/ConfirmDialog'
import { Playground } from './components/Playground'
import { Sidebar } from './components/Sidebar'
import { Badge } from './components/ui'
import { api } from './api'
import type { AgentSummary, Capability, GenerateAgentEvent, TemplateSummary } from './types'

function App() {
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [stages, setStages] = useState<string[]>([])
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [templates, setTemplates] = useState<TemplateSummary[]>([])
  const [loadingAgents, setLoadingAgents] = useState(true)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [showNewAgentModal, setShowNewAgentModal] = useState(false)
  const [pendingDeleteAgentId, setPendingDeleteAgentId] = useState<string | null>(null)
  const [showPlayground, setShowPlayground] = useState(false)

  const refreshAgents = () => {
    setLoadingAgents(true)
    return api
      .listAgents()
      .then((res) => setAgents(res.agents))
      .finally(() => setLoadingAgents(false))
  }

  useEffect(() => {
    refreshAgents()
    api.listStages().then((res) => setStages(res.stages))
    api.listCapabilities().then((res) => setCapabilities(res.capabilities))
    api.listTemplates().then((res) => setTemplates(res.templates))
  }, [])

  const handleCreateAgent = async (agentId: string, skillId: string, purpose: string, templateId: string) => {
    await api.createAgent(agentId, skillId, purpose, templateId)
    await refreshAgents()
    setSelectedAgentId(agentId)
    setShowNewAgentModal(false)
  }

  const handleGenerateAgent = async (
    agentId: string,
    purpose: string,
    onEvent: (event: GenerateAgentEvent) => void,
  ) => {
    const result = await api.generateAgentStream(agentId, purpose, onEvent)
    await refreshAgents()
    if (result.status === 'error') {
      throw new Error(result.error ?? 'Agent generation failed.')
    }
    if (result.status === 'saved_with_errors') {
      throw new Error(`Draft created but failed to load: ${result.error}. Open it from the dashboard to fix it.`)
    }
    setSelectedAgentId(agentId)
    setShowNewAgentModal(false)
  }

  const handleDeleteAgent = (agentId: string) => {
    setPendingDeleteAgentId(agentId)
  }

  const confirmDeleteAgent = async () => {
    const agentId = pendingDeleteAgentId
    if (!agentId) return
    setPendingDeleteAgentId(null)
    await api.deleteAgent(agentId)
    if (selectedAgentId === agentId) setSelectedAgentId(null)
    await refreshAgents()
  }

  const selectedAgent = agents.find((a) => a.agent_id === selectedAgentId) ?? null

  const goToAgents = () => {
    setShowPlayground(false)
    setSelectedAgentId(null)
  }

  return (
    <div className="h-screen flex bg-neutral-50 text-neutral-900">
      <Sidebar
        active={showPlayground && !selectedAgentId ? 'playground' : 'agents'}
        onNavigate={(view) => {
          setSelectedAgentId(null)
          setShowPlayground(view === 'playground')
        }}
      />

      <div className="flex-1 min-w-0">
        {showPlayground ? (
          <div className="flex flex-col h-full">
            <div className="flex items-center gap-2 px-6 py-3.5 border-b border-neutral-200 shrink-0 bg-white">
              <button
                onClick={goToAgents}
                className="text-sm text-neutral-500 hover:text-brand-700 transition-colors cursor-pointer"
              >
                Agents
              </button>
              <span className="text-neutral-300">/</span>
              <span className="text-sm font-medium text-neutral-900">Playground</span>
            </div>
            <div className="flex-1 overflow-hidden p-6">
              <Playground agents={agents} />
            </div>
          </div>
        ) : selectedAgentId ? (
          <div className="flex flex-col h-full">
            <div className="flex items-center gap-2 px-6 py-3.5 border-b border-neutral-200 shrink-0 bg-white">
              <button
                onClick={goToAgents}
                className="text-sm text-neutral-500 hover:text-brand-700 transition-colors cursor-pointer"
              >
                Agents
              </button>
              <span className="text-neutral-300">/</span>
              <span className="text-sm font-medium text-neutral-900 truncate">{selectedAgentId}</span>
              {selectedAgent?.skills && selectedAgent.skills.length > 0 &&
                !(selectedAgent.skills.length === 1 && selectedAgent.skills[0] === selectedAgentId) && (
                <Badge tone="brand" mono>
                  uses {selectedAgent.skills.join(', ')}
                </Badge>
              )}
              {selectedAgent?.error && <Badge tone="danger">error</Badge>}
              {selectedAgent?.draft && <Badge tone="warning">draft — needs review</Badge>}
            </div>

            <div className="flex-1 overflow-hidden">
              <AgentEditor
                key={selectedAgentId}
                agentId={selectedAgentId}
                agents={agents}
                stages={stages}
                capabilities={capabilities}
                templates={templates}
                onSaved={refreshAgents}
              />
            </div>
          </div>
        ) : (
          <Dashboard
            agents={agents}
            loading={loadingAgents}
            onSelect={setSelectedAgentId}
            onNewAgent={() => setShowNewAgentModal(true)}
            onDelete={handleDeleteAgent}
          />
        )}
      </div>

      {showNewAgentModal && (
        <NewAgentModal
          agents={agents}
          templates={templates}
          onClose={() => setShowNewAgentModal(false)}
          onCreate={handleCreateAgent}
          onGenerate={handleGenerateAgent}
        />
      )}

      {pendingDeleteAgentId && (
        <ConfirmDialog
          title={`Delete agent "${pendingDeleteAgentId}"?`}
          message="This removes its agent.yaml but leaves its skill package untouched. This cannot be undone, and may break any workflow that references it by id."
          confirmLabel="Delete"
          onConfirm={confirmDeleteAgent}
          onCancel={() => setPendingDeleteAgentId(null)}
        />
      )}
    </div>
  )
}

export default App
