'use client'

import { Snippet, SnippetCopyButton, SnippetHeader, SnippetTabsContent, SnippetTabsList, SnippetTabsTrigger } from '@/components/kibo-ui/snippet'

// The distribution is `agentdeck-sdk`; the import package is `agentdeck`. PyPI refused
// `agentdeck` as too similar to a squatted placeholder, so the install line is not the import.
const COMMANDS = [
  { key: 'pip', command: 'pip install agentdeck-sdk' },
  { key: 'uv', command: 'uv add agentdeck-sdk' }
]

/** The install line, as a tab per package manager.
 *
 *  One command was a button that copied on click, which is not a control a reader recognises.
 *  Two, because the project is developed on uv and a reader on uv should not have to translate. */
export function InstallLine() {
  return (
    <Snippet className="install-line" defaultValue="pip">
      <SnippetHeader>
        <SnippetTabsList>
          {COMMANDS.map(({ key }) => (
            <SnippetTabsTrigger key={key} value={key}>{key}</SnippetTabsTrigger>
          ))}
        </SnippetTabsList>
        <SnippetCopyButton value={COMMANDS[0].command} />
      </SnippetHeader>
      {COMMANDS.map(({ key, command }) => (
        <SnippetTabsContent key={key} value={key}>{command}</SnippetTabsContent>
      ))}
    </Snippet>
  )
}
