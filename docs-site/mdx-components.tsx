import type { MDXComponents } from 'mdx/types'
import defaultComponents from 'fumadocs-ui/mdx'
import { Callout } from 'fumadocs-ui/components/callout'
import { Steps } from 'fumadocs-ui/components/steps'
import { MentalModel } from './app/diagram'
import { BrandCallout } from './app/callout'
import { Contribute } from './app/contribute'
import {
  SparkMarker,
  DeckSurface,
  AgentDeckCodeBlock,
  RunTimeline,
  StepRail
} from './app/design-system'
import {
  Hero,
  Snippet,
  Foundation,
  Model,
  MeetJack,
  FinalCTA
} from './app/landing-components'

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
