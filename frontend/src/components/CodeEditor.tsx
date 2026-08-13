import CodeMirror from '@uiw/react-codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
import { EditorView } from '@codemirror/view'

type Language = 'yaml' | 'json' | 'markdown'

const extensionsFor = (language: Language) => {
  switch (language) {
    case 'yaml':
      return [yaml()]
    case 'json':
      return [json()]
    case 'markdown':
      return [markdown()]
  }
}

interface CodeEditorProps {
  value: string
  language: Language
  onChange: (value: string) => void
  height?: string
  readOnly?: boolean
}

export function CodeEditor({ value, language, onChange, height = '100%', readOnly = false }: CodeEditorProps) {
  return (
    <CodeMirror
      value={value}
      height={height}
      theme="light"
      extensions={[...extensionsFor(language), EditorView.lineWrapping]}
      onChange={onChange}
      editable={!readOnly}
      basicSetup={{ foldGutter: true, lineNumbers: true }}
    />
  )
}
