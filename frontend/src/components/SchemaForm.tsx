import { useEffect, useState } from 'react'
import { CodeEditor } from './CodeEditor'
import { TextInput } from './ui'
import type { InputSchema, InputSchemaProperty } from '../types'

interface SchemaFormProps {
  schema: InputSchema | undefined
  /** null means the form is currently invalid (a JSON sub-field failed to parse) — callers should disable Run. */
  onChange: (values: Record<string, unknown> | null) => void
  /** Values to seed into the form (e.g. "Fill sample data"). Applied whenever `seedVersion`
   *  increases — the object reference itself isn't watched, so the caller can pass a fresh
   *  object each render without re-seeding on every keystroke. */
  seedValues?: Record<string, unknown> | null
  seedVersion?: number
}

interface FlatRow {
  /** Dot-path used as the fieldText/fieldErrors state key, e.g. "evidence.annual_turnover". */
  key: string
  name: string
  prop: InputSchemaProperty
  depth: number
  isRequired: boolean
  /** An object property with its own declared sub-fields — rendered as a section
   *  label, not an input row; its children (flattened right after it) carry the values. */
  isGroup: boolean
}

function isGroupProperty(prop: InputSchemaProperty): boolean {
  return prop.type === 'object' && !!prop.properties && Object.keys(prop.properties).length > 0
}

function flattenRows(
  properties: Record<string, InputSchemaProperty>,
  requiredSet: Set<string>,
  prefix = '',
  depth = 0,
): FlatRow[] {
  const rows: FlatRow[] = []
  for (const [name, prop] of Object.entries(properties)) {
    const key = prefix ? `${prefix}.${name}` : name
    const isRequired = depth === 0 && requiredSet.has(name)
    const isGroup = isGroupProperty(prop)
    rows.push({ key, name, prop, depth, isRequired, isGroup })
    if (isGroup) {
      rows.push(...flattenRows(prop.properties!, new Set(), key, depth + 1))
    }
  }
  return rows
}

/** Builds the submit payload by walking the schema (not the flat row list) so nested
 *  object properties collect their children into a real nested object — recurses one
 *  level for known shapes (e.g. qualification's `evidence`), but works at any depth. */
function buildNode(
  properties: Record<string, InputSchemaProperty>,
  prefix: string,
  fieldText: Record<string, string>,
): { payload: Record<string, unknown>; errors: Record<string, string | null>; invalid: boolean } {
  const payload: Record<string, unknown> = {}
  const errors: Record<string, string | null> = {}
  let invalid = false

  for (const [name, prop] of Object.entries(properties)) {
    const key = prefix ? `${prefix}.${name}` : name

    if (isGroupProperty(prop)) {
      const nested = buildNode(prop.properties!, key, fieldText)
      Object.assign(errors, nested.errors)
      if (nested.invalid) invalid = true
      if (Object.keys(nested.payload).length > 0) payload[name] = nested.payload
      continue
    }

    const raw = fieldText[key]
    const isEmpty = raw === undefined || raw === ''
    if (isEmpty) {
      errors[key] = null
      continue // omit — never send empty strings/objects for untouched optional fields
    }

    if (prop.type === 'number' || prop.type === 'integer') {
      const num = Number(raw)
      if (Number.isNaN(num)) {
        errors[key] = 'Not a valid number'
        invalid = true
      } else {
        payload[name] = num
        errors[key] = null
      }
    } else if (prop.type === 'boolean') {
      // Every value here comes from the <select> below (true/false), never free text,
      // so this always parses — but stay defensive rather than assume.
      payload[name] = raw === 'true'
      errors[key] = null
    } else if (prop.type === 'object' || prop.type === 'array') {
      try {
        payload[name] = JSON.parse(raw)
        errors[key] = null
      } catch (err) {
        errors[key] = err instanceof Error ? err.message : String(err)
        invalid = true
      }
    } else {
      payload[name] = raw
      errors[key] = null
    }
  }

  return { payload, errors, invalid }
}

/** Inverse of buildNode: turns a nested sample-data object into the flat dot-path
 *  {key: string} shape fieldText expects, walking the schema so it knows which
 *  properties are groups to recurse into vs. leaves to stringify. */
function flattenValueToFieldText(
  properties: Record<string, InputSchemaProperty>,
  values: Record<string, unknown>,
  prefix: string,
  out: Record<string, string>,
): void {
  for (const [name, prop] of Object.entries(properties)) {
    if (!(name in values)) continue
    const key = prefix ? `${prefix}.${name}` : name
    const value = values[name]

    if (isGroupProperty(prop) && value && typeof value === 'object' && !Array.isArray(value)) {
      flattenValueToFieldText(prop.properties!, value as Record<string, unknown>, key, out)
      continue
    }

    if (value === undefined || value === null) continue
    out[key] = typeof value === 'object' ? JSON.stringify(value) : String(value)
  }
}

export function SchemaForm({ schema, onChange, seedValues, seedVersion }: SchemaFormProps) {
  const properties = schema?.properties ?? {}
  const requiredSet = new Set(schema?.required ?? [])
  const hasFields = Object.keys(properties).length > 0

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

    const { payload, errors, invalid } = buildNode(properties, '', fieldText)
    setFieldErrors(errors)
    onChange(invalid ? null : payload)
    // onChange identity is expected to be stable (a setState-derived setter from the parent).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldText, rawText, hasFields])

  useEffect(() => {
    if (!seedVersion || !seedValues) return
    if (!hasFields) {
      setRawText(JSON.stringify(seedValues, null, 2))
      return
    }
    const seeded: Record<string, string> = {}
    flattenValueToFieldText(properties, seedValues, '', seeded)
    setFieldText((cur) => ({ ...cur, ...seeded }))
    // Re-seeding only on seedVersion changing (a click), never on every render — schema/
    // properties/hasFields are stable per agent selection, not meant to re-trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedVersion])

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

  const rows = flattenRows(properties, requiredSet)

  return (
    <div className="grid grid-cols-[minmax(140px,200px)_1fr] gap-x-4 gap-y-3 items-start">
      {rows.map((row) => {
        if (row.isGroup) {
          return (
            <div key={row.key} className="contents">
              <div className="col-span-2 pt-3 first:pt-0 border-t border-neutral-100 first:border-t-0">
                <span className="text-xs font-semibold text-neutral-600 font-mono">{row.name}</span>
                {row.prop.description && (
                  <p className="text-[11px] text-neutral-400 mt-0.5 leading-relaxed">{row.prop.description}</p>
                )}
              </div>
            </div>
          )
        }

        const error = fieldErrors[row.key]
        return (
          <div key={row.key} className="contents">
            <label className="pt-2 text-xs text-neutral-500" style={{ paddingLeft: row.depth * 16 }}>
              <div>
                <span className="font-mono">{row.name}</span>
                {row.isRequired && <span className="text-red-600 ml-1">*</span>}
              </div>
              <div className="text-neutral-400">({row.prop.type})</div>
              {row.prop.description && (
                <p className="text-[11px] text-neutral-400 mt-1 leading-relaxed">{row.prop.description}</p>
              )}
            </label>

            <div>
              {row.prop.type === 'object' || row.prop.type === 'array' ? (
                <div className="h-24 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
                  <CodeEditor
                    value={fieldText[row.key] ?? ''}
                    language="json"
                    onChange={(v) => setFieldText((cur) => ({ ...cur, [row.key]: v }))}
                  />
                </div>
              ) : row.prop.type === 'boolean' ? (
                <select
                  value={fieldText[row.key] ?? ''}
                  onChange={(e) => setFieldText((cur) => ({ ...cur, [row.key]: e.target.value }))}
                  className="w-full rounded-md border border-neutral-300 bg-white text-neutral-900 text-sm px-3 py-2 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                >
                  <option value="">{row.isRequired ? 'required — select…' : 'optional — select…'}</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <TextInput
                  type={row.prop.type === 'number' || row.prop.type === 'integer' ? 'number' : 'text'}
                  value={fieldText[row.key] ?? ''}
                  onChange={(e) => setFieldText((cur) => ({ ...cur, [row.key]: e.target.value }))}
                  placeholder={row.isRequired ? 'required' : 'optional'}
                />
              )}
              {error && <p className="text-[11px] text-red-600 mt-1">{error}</p>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
