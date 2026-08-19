import { Callout, Cards, Steps, Tabs } from 'nextra/components'
import { useMDXComponents as getThemeComponents } from 'nextra-theme-docs'
import { MentalModel } from './app/diagram'
import { BrandCallout } from './app/callout'
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

export function useMDXComponents(components = {}) {
  return {
    ...getThemeComponents(),
    Callout,
    Cards,
    Steps,
    Tabs,
    MentalModel,
    BrandCallout,
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
