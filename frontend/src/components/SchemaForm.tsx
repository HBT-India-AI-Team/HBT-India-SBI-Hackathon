import { useEffect, useState } from 'react'
import { CodeEditor } from './CodeEditor'
import { TextInput } from './ui'
import type { InputSchema } from '../types'

interface SchemaFormProps {
  schema: InputSchema | undefined
  /** null means the form is currently invalid (a JSON sub-field failed to parse) — callers should disable Run. */
  onChange: (values: Record<string, unknown> | null) => void
}

export function SchemaForm({ schema, onChange }: SchemaFormProps) {
  const properties = schema?.properties ?? {}
  const requiredSet = new Set(schema?.required ?? [])
  const propertyEntries = Object.entries(properties)
  const hasFields = propertyEntries.length > 0

  const [fieldText, setFieldText] = useState<Record<string, string>>({})
  const [fieldErrors, setFieldErrors] = useState<Record<string, string | null>>({})
  const [rawText, setRawText] = useState('{\n  \n}')
  const [rawError, setRawError] = useState<string | null>(null)

  useEffect(() => {
    if (!hasFields) {
      try {
        const parsed = rawText.trim() === '' ? {} : JSON.parse(rawText)
        setRawError(null)
        onChange(parsed)
      } catch (err) {
        setRawError(err instanceof Error ? err.message : String(err))
        onChange(null)
      }
      return
    }

    const payload: Record<string, unknown> = {}
    const errors: Record<string, string | null> = {}
    let invalid = false

    for (const [name, prop] of propertyEntries) {
      const raw = fieldText[name]
      const isEmpty = raw === undefined || raw === ''

      if (isEmpty) {
        errors[name] = null
        continue // omit — never send empty strings/objects for untouched optional fields
      }

      if (prop.type === 'number' || prop.type === 'integer') {
        const num = Number(raw)
        if (Number.isNaN(num)) {
          errors[name] = 'Not a valid number'
          invalid = true
        } else {
          payload[name] = num
          errors[name] = null
        }
      } else if (prop.type === 'object' || prop.type === 'array') {
        try {
          payload[name] = JSON.parse(raw)
          errors[name] = null
        } catch (err) {
          errors[name] = err instanceof Error ? err.message : String(err)
          invalid = true
        }
      } else {
        payload[name] = raw
        errors[name] = null
      }
    }

    setFieldErrors(errors)
    onChange(invalid ? null : payload)
    // onChange identity is expected to be stable (a setState-derived setter from the parent).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldText, rawText, hasFields])

  if (!hasFields) {
    return (
      <div className="flex flex-col gap-2">
        <div>
          <h3 className="text-sm font-medium text-neutral-700">Input (raw JSON)</h3>
          <p className="text-xs text-neutral-500 mt-0.5">
            This agent's input_schema declares no fields — enter the raw JSON body to send.
          </p>
        </div>
        <div className="h-32 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
          <CodeEditor value={rawText} language="json" onChange={setRawText} />
        </div>
        {rawError && <p className="text-xs text-red-600">{rawError}</p>}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {propertyEntries.map(([name, prop]) => {
        const isRequired = requiredSet.has(name)
        const error = fieldErrors[name]
        return (
          <div key={name}>
            <label className="block text-xs text-neutral-500 mb-1">
              <span className="font-mono">{name}</span>
              {isRequired && <span className="text-red-600 ml-1">*</span>}
              <span className="text-neutral-400 ml-1.5">({prop.type})</span>
            </label>
            {prop.description && (
              <p className="text-[11px] text-neutral-400 mb-1 leading-relaxed">{prop.description}</p>
            )}

            {prop.type === 'object' || prop.type === 'array' ? (
              <div className="h-24 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
                <CodeEditor
                  value={fieldText[name] ?? ''}
                  language="json"
                  onChange={(v) => setFieldText((cur) => ({ ...cur, [name]: v }))}
                />
              </div>
            ) : (
              <TextInput
                type={prop.type === 'number' || prop.type === 'integer' ? 'number' : 'text'}
                value={fieldText[name] ?? ''}
                onChange={(e) => setFieldText((cur) => ({ ...cur, [name]: e.target.value }))}
                placeholder={isRequired ? 'required' : 'optional'}
              />
            )}
            {error && <p className="text-[11px] text-red-600 mt-1">{error}</p>}
          </div>
        )
      })}
    </div>
  )
}
