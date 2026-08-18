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
  icon: string;
};

const modules: ModuleDef[] = [
  { id: "agent", label: "Agent", x: 12, y: 18, icon: "A" },
  { id: "tool", label: "Tool", x: 76, y: 22, icon: "T" },
  { id: "workflow", label: "Workflow", x: 8, y: 66, icon: "W" },
  { id: "skill", label: "Skill", x: 79, y: 70, icon: "S" },
];

const phases: Array<{ name: Phase; ms: number }> = [
  { name: "scatter", ms: 1300 },
  { name: "compose", ms: 1500 },
  { name: "deck", ms: 900 },
  { name: "activate", ms: 900 },
  { name: "execute", ms: 3200 },
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

    const loop = async () => {
      while (!cancelled) {
        for (const item of phases) {
          if (cancelled) return;
          setPhase(item.name);
          await new Promise<void>((resolve) => {
            timer = window.setTimeout(resolve, item.ms);
          });
        }
      }
    };

    void loop();

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
      <span className="ad-module__icon">{item.icon}</span>
      <span>{item.label}</span>
    </div>
  );
}

function ConnectionLayer({ phase }: { phase: Phase }) {
  const visible = phase !== "scatter";
  return (
    <svg className={`ad-connections ${visible ? "is-visible" : ""}`} viewBox="0 0 1000 420" preserveAspectRatio="none">
      <path d="M160 90 C310 90 360 165 500 205" />
      <path d="M840 105 C690 105 650 165 500 205" />
      <path d="M125 310 C280 310 365 250 500 205" />
      <path d="M875 320 C710 320 640 250 500 205" />
    </svg>
  );
}

function ExecutionTree({ active }: { active: boolean }) {
  return (
    <div className={`ad-tree ${active ? "is-active" : ""}`} aria-hidden={!active}>
      <svg className="ad-tree__lines" viewBox="0 0 760 260" preserveAspectRatio="none">
        <path className="edge edge-1" d="M380 0 V45" />
        <path className="edge edge-2" d="M380 45 V85 H145 V125" />
        <path className="edge edge-3" d="M380 45 V125" />
        <path className="edge edge-4" d="M380 45 V85 H615 V125" />
        <path className="edge edge-5" d="M145 170 V222" />
        <path className="edge edge-6" d="M380 170 V222" />
        <path className="edge edge-7" d="M615 170 V222" />
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
  const showDeck = phase === "deck" || activeDeck;
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
          <ConnectionLayer phase={phase} />

          <div className="ad-stage__modules">
            {modules.map((item) => (
              <ModuleCard key={item.id} item={item} phase={phase} />
            ))}
          </div>

          <div className={`ad-deck ${showDeck ? "is-visible" : ""} ${activeDeck ? "is-active" : ""}`}>
            <div className="ad-deck__layer ad-deck__layer--back" />
            <div className="ad-deck__layer ad-deck__layer--mid" />
            <div className="ad-deck__surface">
              <DeckMark active={activeDeck} />
              <span className="ad-deck__label">DECK</span>
            </div>

            <div className={`ad-spark ${phase === "activate" ? "is-flying" : ""}`}>
              <DeckMark active />
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
