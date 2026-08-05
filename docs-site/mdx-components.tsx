import { Callout, Cards, Steps, Tabs } from 'nextra/components'
import { useMDXComponents as getThemeComponents } from 'nextra-theme-docs'

// Registered globally so pages use <Callout> etc. without an import line in every file.
export function useMDXComponents(components = {}) {
  return {
    ...getThemeComponents(),
    Callout,
    Cards,
    Steps,
    Tabs,
    ...components
  }
}
