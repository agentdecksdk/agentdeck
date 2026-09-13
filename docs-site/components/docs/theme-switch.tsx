'use client'

import { useTheme } from 'next-themes'
import { ThemeSwitcher } from '@/components/kibo-ui/theme-switcher'

/** The theme control, bound to the theme that is actually applied.
 *
 *  `ui/theme-switcher` is uncontrolled by design: it tracks a key and calls back, and never
 *  touches `next-themes`. Left alone it renders a switch that highlights a pill and changes
 *  nothing, which is the shape every registry component arrives in. */
export function ThemeSwitch({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme()
  return (
    <ThemeSwitcher
      className={className}
      value={(theme as 'light' | 'dark' | 'system') ?? 'system'}
      onChange={setTheme}
    />
  )
}
