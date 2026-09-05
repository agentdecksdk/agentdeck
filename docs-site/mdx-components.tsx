import type { MDXComponents } from 'mdx/types'
import defaultComponents from 'fumadocs-ui/mdx'
import { Callout } from 'fumadocs-ui/components/callout'
import { Steps } from 'fumadocs-ui/components/steps'
import { MentalModel } from '@/components/docs/diagram'
import { BrandCallout } from '@/components/docs/callout'
import { Contribute } from '@/components/docs/contribute'
import {
  SparkMarker,
  DeckSurface,
  AgentDeckCodeBlock,
  RunTimeline,
  StepRail
} from '@/components/docs/design-system'
import {
  Hero,
  Snippet,
  Foundation,
  Model,
  MeetJack,
  FinalCTA
} from '@/components/landing/blocks'

// `Cards` and `Tabs` were exported and never used by any of the 44 pages.
export function getMDXComponents(components: MDXComponents = {}): MDXComponents {
  return {
    ...defaultComponents,
    Callout,
    Steps,
    MentalModel,
    BrandCallout,
    Contribute,
    SparkMarker,
    DeckSurface,
    AgentDeckCodeBlock,
    RunTimeline,
    StepRail,
    Hero,
    Snippet,
    Foundation,
    Model,
    MeetJack,
    FinalCTA,
    ...components
  }
}
