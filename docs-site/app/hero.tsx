'use client'

/**
 * The hero: capabilities scatter, compose into a Deck, the Deck is activated, and execution
 * grows out of it. No code, because the page has not taught any yet.
 *
 * The mark is the real spark from `docs/brand/components/spark.svg`, pasted in the mark space it
 * is drawn for, so the animation is the brand rather than an approximation of it.
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'

type Phase = "scatter" | "compose" | "deck" | "activate" | "execute";

type ModuleKey = "agent" | "tool" | "workflow" | "skill";

type ModuleDef = {
  id: ModuleKey;
  label: string;
  x: number;
  y: number;
};

/**
 * One glyph per kind, drawn in the same geometry as the rest of the page: thin structural
 * lines, nodes, and the mark's 45-degree cut corner. A letter in a box said nothing that the
 * label beside it did not already say.
 */
const ICONS: Record<ModuleKey, React.ReactNode> = {
  // A node that decides: a hexagon holding a single point.
  agent: (
    <>
      <path d="M8 2 13.2 5 13.2 11 8 14 2.8 11 2.8 5Z" />
      <circle cx="8" cy="8" r="1.6" fill="currentColor" stroke="none" />
    </>
  ),
  // A thing that does work.
  tool: (
    <>
      <circle cx="5.6" cy="10.4" r="3.2" />
      <path d="M8 8.1 13.2 2.9" />
    </>
  ),
  // Process: one step in, two steps out.
  workflow: (
    <>
      <path d="M4.6 8H8m0 0V4.6h3.4M8 8v3.4h3.4" />
      <circle cx="3" cy="8" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="13" cy="4.6" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="13" cy="11.4" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
  // Something written down, on the mark's own cut-corner card.
  skill: (
    <>
      <path d="M3.4 2.2h6.7l2.5 2.5v9.1H3.4Z" />
      <path d="M5.9 7.6h4.2M5.9 10.3h3" />
    </>
  ),
};

const modules: ModuleDef[] = [
  { id: "agent", label: "Agent", x: 12, y: 18 },
  { id: "tool", label: "Tool", x: 76, y: 22 },
  { id: "workflow", label: "Workflow", x: 14, y: 66 },
  { id: "skill", label: "Skill", x: 79, y: 70 },
];

const phases: Array<{ name: Phase; ms: number }> = [
  { name: "scatter", ms: 1650 },
  { name: "compose", ms: 2300 },
  { name: "deck", ms: 650 },
  { name: "activate", ms: 520 },
  { name: "execute", ms: 4600 },
];

function useHeroPhase() {
  const [phase, setPhase] = useState<Phase>("scatter");

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setPhase("execute");
      return;
    }

    let cancelled = false;
    let timer: number | undefined;

    // Once, then it holds on the last frame. A hero that keeps restarting asks to be watched
    // again every time the reader scrolls back, and the end state is the one that says what
    // the page is about.
    const play = async () => {
      for (const item of phases) {
        if (cancelled) return;
        setPhase(item.name);
        if (item === phases[phases.length - 1]) return;
        await new Promise<void>((resolve) => {
          timer = window.setTimeout(resolve, item.ms);
        });
      }
    };

    void play();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  return phase;
}

function DeckMark({ active = false }: { active?: boolean }) {
  return (
    <svg
      className={`ad-mark ${active ? "is-active" : ""}`}
      viewBox="880 152.5 196.8 196.7"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M 998.23,319.84 l -9.95,22.78 c -3.82,8.76 -15.94,8.76 -19.77,0 l -9.95,-22.78 c -8.86,-20.28 -24.79,-36.42 -44.66,-45.24 L 886.53,262.45 c -8.7,-3.86 -8.7,-16.53 0,-20.4 l 26.52,-11.77 C 933.44,221.23 949.65,204.5 958.36,183.54 l 10.07,-24.28 c 3.74,-9.01 16.19,-9.01 19.93,0 L 998.43,183.54 c 8.7,20.97 24.92,37.71 45.31,46.75 l 26.52,11.77 c 8.7,3.86 8.7,16.53 0,20.4 l -27.38,12.16 C 1023.02,283.43 1007.08,299.57 998.23,319.84 z" />
    </svg>
  )
}

/**
 * The Deck, drawn as the mark itself: the ace-cut card in outline with the A knocked out of it,
 * and the spark in Ace Red. Both paths are copied verbatim from `docs/brand/components/`, in the
 * mark space they are drawn for, so this is the logo rather than a card that resembles it.
 *
 * `card` alone is what the layers behind the front one carry, since only the front one is the
 * whole lockup.
 */
const CARD_PATH =
  "M 369,202.6 c -1.4,0.2 -6.3,0.9 -11,1.5 c -25.6,3.4 -49.4,15.4 -69.7,35.2 c -19.5,19 -31.9,41.6 -37.4,68.7 c -3.5,17.1 -4.2,79.3 -4.3,384.5 c -0.1,269.5 0,282.4 1.7,290.3 c 5.3,24.1 16.5,44.1 34.3,61.2 c 17.8,17.1 42.4,28.7 67.6,32 c 5.8,0.7 91.5,1 283.3,0.8 l 275,-0.3 l 11.8,-3.2 c 12.9,-3.5 28.9,-10.7 38.8,-17.4 c 8.1,-5.5 21.7,-18.5 28,-26.9 c 5.7,-7.4 14.8,-25.4 17.8,-35 c 5.3,-16.8 5.1,-7.7 5.1,-254.7 c 0,-166.4 -0.3,-230.7 -1.1,-234.5 c -0.6,-2.9 -2.5,-8.2 -4.2,-11.8 c -2.7,-6 -9,-12.6 -88.7,-92.5 c -77.3,-77.6 -130.9,-131.7 -170.5,-172.3 c -15.6,-16.1 -19.5,-19.1 -29.2,-23 l -6.8,-2.7 l -169,-0.1 c -92.9,-0.1 -170.1,0 -171.5,0.2 z m 273.2,275.4 c 8.1,2.5 19.1,10.2 23.9,16.6 c 2,2.7 11.6,20 21.4,38.4 c 30.6,57.7 34.6,65.2 40,75 c 2.9,5.2 9,16.5 13.5,25 c 10.7,20.1 18.8,35.1 34.5,64 c 7,12.9 19.7,36.5 28.3,52.5 c 8.5,15.9 29.2,54.2 46,85 c 16.7,30.8 32.2,59.2 34.3,63.2 c 9.6,17.7 8.9,35.6 -1.8,51.2 c -4.3,6.2 -12.9,12.3 -20.7,14.6 c -7.5,2.3 -61,2.2 -68.6,-0.1 c -9.4,-2.8 -19.4,-11.4 -24.1,-20.6 c -1.2,-2.3 -4.5,-8.4 -7.4,-13.3 c -4.8,-8.2 -10.8,-18.9 -30.5,-54 c -3.7,-6.6 -10.9,-19.4 -16,-28.5 c -9.1,-16.3 -16.9,-30.3 -31.4,-56.3 c -4,-7.2 -11.6,-21.1 -16.8,-30.7 c -5.2,-9.6 -11.4,-21 -13.8,-25.4 c -5.3,-9.8 -8.9,-13.9 -14.3,-16.4 c -10,-4.8 -20.4,-3.3 -28.2,3.9 c -4.5,4.1 -6.6,7.7 -26.8,44.4 c -6.3,11.5 -16.3,29.5 -22.2,40 c -6,10.4 -14.7,26 -19.5,34.5 c -4.8,8.5 -20.5,36.2 -34.9,61.5 c -28.8,50.5 -31.4,54 -42.5,59.2 l -6.1,2.8 l -33.5,0 l -33.5,0 l -5.8,-2.7 c -18.7,-8.8 -28.7,-32.3 -22.5,-52.8 c 0.9,-3.1 16.3,-31.9 29.8,-56 c 4.6,-8.3 12.9,-23.4 32.2,-59 c 5.3,-9.6 16.4,-30.3 24.8,-46 c 8.4,-15.7 18.6,-34.8 22.8,-42.5 c 4.1,-7.7 9,-16.7 10.7,-20 c 1.8,-3.3 9.1,-16.8 16.2,-30 c 7.2,-13.2 18.3,-33.9 24.8,-46 c 6.5,-12.1 15,-27.9 18.8,-35 c 12.6,-23.2 22,-40.7 31.7,-58.9 c 11,-20.6 17.2,-28.3 27.1,-33.6 c 8.5,-4.5 13.8,-5.8 24.5,-5.9 c 6.6,-0.1 11,0.5 15.6,1.9 z";
const SPARK_PATH =
  "M 998.23,319.84 l -9.95,22.78 c -3.82,8.76 -15.94,8.76 -19.77,0 l -9.95,-22.78 c -8.86,-20.28 -24.79,-36.42 -44.66,-45.24 L 886.53,262.45 c -8.7,-3.86 -8.7,-16.53 0,-20.4 l 26.52,-11.77 C 933.44,221.23 949.65,204.5 958.36,183.54 l 10.07,-24.28 c 3.74,-9.01 16.19,-9.01 19.93,0 L 998.43,183.54 c 8.7,20.97 24.92,37.71 45.31,46.75 l 26.52,11.77 c 8.7,3.86 8.7,16.53 0,20.4 l -27.38,12.16 C 1023.02,283.43 1007.08,299.57 998.23,319.84 z";

function DeckLockup() {
  return (
    <svg className="ad-card ad-card--lockup" viewBox="246 145 832 933" fill="none" role="img" aria-label="AgentDeck">
      <path d={CARD_PATH} />
      <path className="ad-card__spark" d={SPARK_PATH} />
    </svg>
  )
}

function ModuleCard({
  item,
  phase,
}: {
  item: ModuleDef;
  phase: Phase;
}) {
  const composing = phase !== "scatter";
  return (
    <div
      className={`ad-module ad-module--${item.id} ${composing ? "is-composing" : ""}`}
      style={
        {
          "--scatter-x": `${item.x}%`,
          "--scatter-y": `${item.y}%`,
        } as React.CSSProperties
      }
    >
      <span className="ad-module__icon">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {ICONS[item.id]}
        </svg>
      </span>
      <span>{item.label}</span>
    </div>
  );
}

function ExecutionTree({ active }: { active: boolean }) {
  return (
    <div className={`ad-tree ${active ? "is-active" : ""}`} aria-hidden={!active}>
      <svg className="ad-tree__lines" viewBox="0 0 660 300" preserveAspectRatio="none">
        <path className="edge edge-1" d="M0 150 H80" />
        <path className="edge edge-2" d="M192 150 H250 V46 H310" />
        <path className="edge edge-3" d="M192 150 H310" />
        <path className="edge edge-4" d="M192 150 H250 V254 H310" />
        <path className="edge edge-5" d="M422 46 H530" />
        <path className="edge edge-6" d="M422 150 H530" />
        <path className="edge edge-7" d="M422 254 H530" />
      </svg>

      <div className="tree-node tree-node--run">
        <span className="status status--running" />
        Run
      </div>

      <div className="tree-node tree-node--agent">Agent</div>
      <div className="tree-node tree-node--tool">Tool</div>
      <div className="tree-node tree-node--workflow">Workflow</div>

      <div className="tree-node tree-node--leaf-a">
        <span className="status status--done" />
        Done
      </div>
      <div className="tree-node tree-node--leaf-b">
        <span className="status status--running" />
        Running
      </div>
      <div className="tree-node tree-node--leaf-c">
        <span className="status status--queued" />
        Queued
      </div>
    </div>
  );
}

export function Hero() {
  const phase = useHeroPhase();

  const activeDeck = phase === "activate" || phase === "execute";
  // Visible from the moment the capabilities start converging, so they merge into a Deck
  // rather than into each other. Its reveal is delayed in CSS to land as they arrive.
  const showDeck = phase !== "scatter";
  const showTree = phase === "execute";

  const phaseLabel = useMemo(() => {
    switch (phase) {
      case "scatter":
        return "Capabilities";
      case "compose":
        return "Compose";
      case "deck":
        return "Deck";
      case "activate":
        return "Activate";
      case "execute":
        return "Execute";
    }
  }, [phase]);

  return (
    <section className="ad-hero">
      <div className="ad-hero__backdrop" aria-hidden="true" />

      <div className="ad-hero__content">
        <div className="ad-hero__eyebrow">
          <DeckMark active />
          AgentDeck
        </div>

        <h1>Agentic software should feel like software.</h1>

        <p className="ad-hero__subtitle">
          Build agents, tools and workflows as normal software.
          AgentDeck gives them one execution model you can observe,
          control and extend.
        </p>

        <div className="ad-hero__actions">
          <Link className="ad-btn ad-btn--primary" href="/meet-agentdeck/quickstart">
            Get started
          </Link>
          <a className="ad-btn ad-btn--ghost" href="https://github.com/agentdecksdk/agentdeck" target="_blank" rel="noreferrer">
            GitHub
          </a>
        </div>

        <div className={`ad-stage phase-${phase}`} aria-label={`AgentDeck composition animation: ${phaseLabel}`}>
          <div className="ad-stage__grid" aria-hidden="true" />

          <div className="ad-stage__modules">
            {modules.map((item) => (
              <ModuleCard key={item.id} item={item} phase={phase} />
            ))}
          </div>

          <div className={`ad-deck ${showDeck ? "is-visible" : ""} ${activeDeck ? "is-active" : ""}`}>
            <div className="ad-deck__surface">
              <DeckLockup />
              <span className="ad-deck__label">DECK</span>
            </div>
          </div>

          <ExecutionTree active={showTree} />

          <div className="ad-stage__caption">
            <span className="ad-stage__caption-dot" />
            {phaseLabel}
          </div>
        </div>
      </div>
    </section>
  );
}
