# AgentDeck Design System

A minimal, architectural design system for AgentDeck documentation, surfaces, and developer tooling.

## 1. Core Principles

1. **Function-Driven Design**: Every element earns its place on screen. The runtime is the hero.
2. **Restrained Precision**: High technical density, clean typography, thin structural lines, and intentional whitespace.
3. **Semantic Geometry**: Geometry derives directly from the AgentDeck logo (cut corners, diamond sparks).
4. **State-Driven Motion**: Motion represents real execution transitions, never decorative fluff.

## 2. Design Tokens

### Geometry Tokens
- **Cut Corner**: 45-degree chamfer on outer surfaces (`polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)`).
- **Spark / Diamond**: `◇` (structural / inactive), `◆` (active / selected / transition), `●` (live edge).
- **Structural Lines**: 1px crisp borders in `#e5e9f2` (light) / `#1e293b` (dark).

### Color Tokens
- **Brand Blue**: `#2563ff` (Agent Blue: active, live, primary accent).
- **Brand Frost**: `#93b5ff` (Frost Blue: dark-mode accent and text highlights).
- **Brand Night**: `#0b1220` (Deep Night: dark-mode canvas).
- **Brand Slate**: `#162032` (Slate: dark-mode elevated surfaces).
- **Brand Ice**: `#f1f5fd` (Ice: light-mode elevated surfaces).
- **Brand Canvas**: `#fafbfe` (Snow: light-mode canvas).
- **Status Colors**:
  - Success / Done: `#22c55e`
  - Waiting / Input: `#f59e0b`
  - Cancelled / Error: `#ef4444`

### Typography Tokens
- **Display & Headings**: Inter / System Sans, tight tracking (`-0.025em` to `-0.035em`), font-weight 600-700.
- **Body**: Clean readable sans-serif, line-height 1.6.
- **Monospace**: `SFMono-Regular, Menlo, Monaco, Consolas, monospace` (strictly for identifiers, tools, events, code).

## 3. Spark & Diamond Grammar

The spark is not decoration. It carries functional semantics across the documentation:

| Glyph | Semantic Meaning | Example Use Cases |
| :--- | :--- | :--- |
| `◇` | Structural / Inactive / Standard | Unselected category, past event, declared tool, note header |
| `◆` | Active / Selected / Transition | Active category, current step, tool call, runtime waiting gate |
| `●` | Live Edge / Execution Head | Real-time stream cursor, active timer marker |

## 4. Component Catalog

1. **`SparkMarker`**: Renders semantic `◇`, `◆`, or `●` markers with theme-aware styling.
2. **`DeckSurface`**: Chamfered surface container for key product concepts and architectures.
3. **`AgentDeckCodeBlock`**: Branded code container with file path label, spark indicator, and copy action.
4. **`RunPlayer`**: Cinematic live execution player streaming real runtime state.
5. **`RunTimeline`**: Execution timeline tracking ordered lifecycle events with diamond nodes.
6. **`ConceptFlow`**: Architectural flow component for `Agents + Tools + Workflows + Skills → Deck → Run`.
7. **`BrandedCallout`**: Styled callouts for `AGENTDECK MODEL`, `RUNTIME BEHAVIOR`, and `GATE`.
8. **`StepRail`**: Numbered step container with spark markers for guided quickstart flows.

## 5. Anti-Patterns

- Do not use arbitrary floating cards with heavy dropped shadows.
- Do not use purple or neon gradient text.
- Do not use rainbow color palettes for event types.
- Do not use em dashes in text; use regular hyphens, colons, or clean phrasing.
- Do not add animations that continue looping while a Run is paused or waiting.
