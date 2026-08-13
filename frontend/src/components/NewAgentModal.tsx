import { useMemo, useState } from 'react'
import { Button, ChoiceCard, FieldLabel, Modal, TextArea, TextInput } from './ui'
import { ProgressChecklist, upsertStep, type ProgressStep } from './ProgressChecklist'
import type { ArchetypeSummary, AgentSummary, GenerateAgentEvent, TemplateSummary } from '../types'

interface NewAgentModalProps {
  agents: AgentSummary[]
  templates: TemplateSummary[]
  archetypes: ArchetypeSummary[]
  onClose: () => void
  onCreate: (agentId: string, skillId: string, purpose: string, templateId: string) => Promise<void>
  onGenerate: (
    agentId: string,
    purpose: string,
    archetypeId: string,
    onEvent: (event: GenerateAgentEvent) => void,
  ) => Promise<void>
}

type SkillMode = 'new' | 'existing'
type BuildMode = 'template' | 'describe'

function stepsFromEvent(prev: ProgressStep[], event: GenerateAgentEvent): ProgressStep[] {
  if (event.step === 'decompose') {
    return upsertStep(prev, 'decompose', 'Reading your description', event.status === 'done' ? 'done' : 'active')
  }
  if (event.step === 'generate_skill') {
    const label = event.total && event.total > 1
      ? `Drafting rules for "${event.skill_id}" (${event.index}/${event.total})`
      : 'Drafting the rules'
    return upsertStep(prev, `skill:${event.skill_id}`, label, event.status === 'done' ? 'done' : 'active')
  }
  if (event.step === 'save') {
    return upsertStep(prev, 'save', 'Saving the agent', event.status === 'done' ? 'done' : 'active')
  }
  if (event.step === 'validate') {
    return upsertStep(prev, 'validate', 'Checking it loads cleanly', event.status === 'error' ? 'error' : event.status === 'done' ? 'done' : 'active')
  }
  return prev
}

export function NewAgentModal({ agents, templates, archetypes, onClose, onCreate, onGenerate }: NewAgentModalProps) {
  const existingSkills = useMemo(() => {
    const byId = new Map<string, string[]>()
    for (const agent of agents) {
      for (const skillId of agent.skills ?? []) {
        byId.set(skillId, [...(byId.get(skillId) ?? []), agent.agent_id])
      }
    }
    return Array.from(byId, ([skillId, usedBy]) => ({ skillId, usedBy })).sort((a, b) =>
      a.skillId.localeCompare(b.skillId),
    )
  }, [agents])

  const [buildMode, setBuildMode] = useState<BuildMode>('template')
  const [agentId, setAgentId] = useState('')
  const [purpose, setPurpose] = useState('')
  const [templateId, setTemplateId] = useState('blank')
  const [archetypeId, setArchetypeId] = useState('qualification')
  const [skillMode, setSkillMode] = useState<SkillMode>('new')
  const [newSkillId, setNewSkillId] = useState('')
  const [existingSkillId, setExistingSkillId] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([])

  const selectedTemplate = templates.find((t) => t.id === templateId) ?? null
  const selectedArchetype = archetypes.find((a) => a.id === archetypeId) ?? null

  const resolvedSkillId =
    skillMode === 'existing' ? existingSkillId : newSkillId.trim() || agentId.trim()
  const canSubmit =
    buildMode === 'describe'
      ? agentId.trim().length > 0 && purpose.trim().length > 0 && !submitting
      : agentId.trim().length > 0 && !!resolvedSkillId && !submitting

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      if (buildMode === 'describe') {
        setProgressSteps([])
        await onGenerate(agentId.trim(), purpose.trim(), archetypeId, (event) => {
          setProgressSteps((prev) => stepsFromEvent(prev, event))
        })
      } else {
        if (!resolvedSkillId) return
        await onCreate(agentId.trim(), resolvedSkillId, purpose.trim(), templateId)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal onClose={onClose}>
        <h2 className="text-neutral-900 font-semibold mb-1">New Agent</h2>
        <p className="text-xs text-neutral-500 mb-4 leading-relaxed">
          {buildMode === 'describe'
            ? <>An AI drafts a real {selectedArchetype ? selectedArchetype.label.toLowerCase() : 'agent'} from
              your description{selectedArchetype ? ` — ${selectedArchetype.description}` : ''} It always lands as
              a draft — review it in the editor before it's trusted or routable.</>
            : <>Scaffolds agents/&lt;id&gt;/agent.yaml with a starter pipeline
              {selectedTemplate ? ` (${selectedTemplate.pipeline.join(' → ')})` : ''}, plus a skill
              package it runs against.</>}
        </p>

        <FieldLabel>Agent name</FieldLabel>
        <TextInput
          className="mb-4"
          placeholder="e.g. financial_literacy"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
        />

        <label className="block text-xs text-neutral-400 mb-2">How do you want to build it?</label>
        <div className="grid grid-cols-2 gap-2 mb-4">
          <ChoiceCard
            selected={buildMode === 'template'}
            onClick={() => setBuildMode('template')}
            title="From template"
            subtitle="Pick a starting shape, fill in rules yourself"
          />
          <ChoiceCard
            selected={buildMode === 'describe'}
            onClick={() => setBuildMode('describe')}
            title="Describe it"
            subtitle="AI drafts gates/scoring — creates a draft to review"
          />
        </div>

        {buildMode === 'template' ? (
          <>
            {templates.length > 0 && (
              <>
                <label className="block text-xs text-neutral-400 mb-2">Template</label>
                <div className="grid grid-cols-2 gap-2 mb-4">
                  {templates.map((template) => (
                    <ChoiceCard
                      key={template.id}
                      selected={templateId === template.id}
                      onClick={() => setTemplateId(template.id)}
                      title={template.label}
                      subtitle={template.description}
                    />
                  ))}
                </div>
              </>
            )}

            <label className="block text-xs text-neutral-400 mb-2">Skill package</label>
            <div className="grid grid-cols-2 gap-2 mb-3">
              <ChoiceCard
                selected={skillMode === 'new'}
                onClick={() => setSkillMode('new')}
                title="Create new"
                subtitle="Scaffold a fresh skill for this agent"
              />
              <ChoiceCard
                selected={skillMode === 'existing'}
                disabled={existingSkills.length === 0}
                onClick={() => {
                  setSkillMode('existing')
                  setExistingSkillId((cur) => cur ?? existingSkills[0]?.skillId ?? null)
                }}
                title="Reuse existing"
                subtitle={existingSkills.length === 0 ? 'No skills yet' : 'Point this agent at an existing skill'}
              />
            </div>

            {skillMode === 'new' ? (
              <TextInput
                className="mb-4"
                placeholder="Skill id (defaults to agent name)"
                value={newSkillId}
                onChange={(e) => setNewSkillId(e.target.value)}
              />
            ) : (
              <div className="mb-4 max-h-40 overflow-y-auto grid grid-cols-2 gap-2 pr-0.5">
                {existingSkills.map(({ skillId, usedBy }) => (
                  <ChoiceCard
                    key={skillId}
                    mono
                    selected={existingSkillId === skillId}
                    onClick={() => setExistingSkillId(skillId)}
                    title={skillId}
                    subtitle={`used by ${usedBy.length} agent${usedBy.length === 1 ? '' : 's'}`}
                  />
                ))}
              </div>
            )}

            <FieldLabel>Purpose</FieldLabel>
            <TextArea
              className="mb-4"
              rows={3}
              placeholder="One paragraph describing what this agent does."
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            />
          </>
        ) : (
          <>
            {archetypes.length > 0 && (
              <>
                <label className="block text-xs text-neutral-400 mb-2">What kind of agent is this?</label>
                <div className="grid grid-cols-2 gap-2 mb-4">
                  {archetypes.map((archetype) => (
                    <ChoiceCard
                      key={archetype.id}
                      selected={archetypeId === archetype.id}
                      onClick={() => setArchetypeId(archetype.id)}
                      title={archetype.label}
                      subtitle={archetype.description}
                      disabled={submitting}
                    />
                  ))}
                </div>
              </>
            )}

            <FieldLabel>Describe what this agent should do</FieldLabel>
            <TextArea
              className="mb-4"
              rows={6}
              placeholder={
                archetypeId === 'conversational'
                  ? 'e.g. Answer employee questions about the bank\'s leave policy, given a short policy document as context. Escalate to HR if the question is about a personal dispute.'
                  : 'e.g. Qualify SME loan applicants based on debt ratio and credit score. Reject anyone with an active default. Recommend a working capital loan for strong applicants.'
              }
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              disabled={submitting}
            />
            {submitting && progressSteps.length > 0 && <ProgressChecklist steps={progressSteps} />}
          </>
        )}

        {error && (
          <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2.5 mb-3">{error}</p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSubmit} disabled={!canSubmit}>
            {buildMode === 'describe'
              ? submitting ? 'Generating…' : 'Generate'
              : submitting ? 'Creating…' : 'Create'}
          </Button>
        </div>
    </Modal>
  )
}
