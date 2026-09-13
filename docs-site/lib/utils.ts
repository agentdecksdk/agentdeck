import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn's convention: merge conditional classes, the last Tailwind utility of a group winning. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
