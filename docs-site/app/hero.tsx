'use client'

/**
 * The hero: capabilities scatter, compose into a Deck, the Deck is activated, and execution
 * grows out of it. No code, because the page has not taught any yet.
 *
 * The mark is the real spark from `docs/brand/components/spark.svg`, pasted in the mark space it
 * is drawn for, so the animation is the brand rather than an approximation of it.
 */

import { useEffect, useState } from 'react'
import Link from 'next/link'

import { ICONS, type ModuleKey } from './kind-icons'

type Phase = "scatter" | "compose" | "deck" | "activate" | "execute";

type ModuleDef = {
  id: ModuleKey;
  label: string;
  x: number;
  y: number;
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

/**
 * The wordmark, as the nine glyph outlines `docs/brand/components/wordmark.svg` holds. Outlines
 * rather than live text for the reason the brand README gives: a renderer without Poppins
 * substitutes silently instead of failing.
 */
function Wordmark() {
  return (
    <svg className="ad-wordmark" viewBox="0 95 516.86 143" fill="currentColor" role="img" aria-label="agentdeck">
      <path d="M27.46 145.95Q33.41 145.95 37.87 148.35Q42.34 150.75 45.02 154.4V146.82H58.56V200H45.02V192.22Q42.43 195.97 37.87 198.42Q33.31 200.86 27.36 200.86Q20.64 200.86 15.12 197.41Q9.6 193.95 6.38 187.66Q3.17 181.38 3.17 173.22Q3.17 165.15 6.38 158.91Q9.6 152.67 15.12 149.31Q20.64 145.95 27.46 145.95ZM30.91 157.76Q27.17 157.76 24 159.58Q20.83 161.41 18.86 164.91Q16.9 168.42 16.9 173.22Q16.9 178.02 18.86 181.62Q20.83 185.22 24.05 187.14Q27.26 189.06 30.91 189.06Q34.66 189.06 37.92 187.18Q41.18 185.31 43.1 181.81Q45.02 178.3 45.02 173.41Q45.02 168.51 43.1 165.01Q41.18 161.5 37.92 159.63Q34.66 157.76 30.91 157.76Z" />
      <path d="M108.18 154.4V146.82H121.72V200.38Q121.72 207.78 118.74 213.58Q115.76 219.39 109.81 222.8Q103.86 226.21 95.41 226.21Q84.08 226.21 76.84 220.93Q69.59 215.65 68.63 206.53H81.97Q83.03 210.18 86.53 212.34Q90.04 214.5 95.03 214.5Q100.88 214.5 104.53 210.99Q108.18 207.49 108.18 200.38V192.13Q105.59 195.87 101.03 198.37Q96.47 200.86 90.61 200.86Q83.89 200.86 78.32 197.41Q72.76 193.95 69.54 187.66Q66.32 181.38 66.32 173.22Q66.32 165.15 69.54 158.91Q72.76 152.67 78.28 149.31Q83.8 145.95 90.61 145.95Q96.56 145.95 101.08 148.3Q105.59 150.66 108.18 154.4ZM94.07 157.76Q90.32 157.76 87.16 159.58Q83.99 161.41 82.02 164.91Q80.05 168.42 80.05 173.22Q80.05 178.02 82.02 181.62Q83.99 185.22 87.2 187.14Q90.42 189.06 94.07 189.06Q97.81 189.06 101.08 187.18Q104.34 185.31 106.26 181.81Q108.18 178.3 108.18 173.41Q108.18 168.51 106.26 165.01Q104.34 161.5 101.08 159.63Q97.81 157.76 94.07 157.76Z" />
      <path d="M182.01 177.44H143.13Q143.61 183.2 147.16 186.46Q150.71 189.73 155.9 189.73Q163.38 189.73 166.55 183.3H181.05Q178.74 190.98 172.22 195.92Q165.69 200.86 156.18 200.86Q148.5 200.86 142.41 197.46Q136.31 194.05 132.9 187.81Q129.5 181.57 129.5 173.41Q129.5 165.15 132.86 158.91Q136.22 152.67 142.26 149.31Q148.31 145.95 156.18 145.95Q163.77 145.95 169.77 149.22Q175.77 152.48 179.08 158.48Q182.39 164.48 182.39 172.26Q182.39 175.14 182.01 177.44ZM168.47 168.42Q168.38 163.23 164.73 160.11Q161.08 156.99 155.8 156.99Q150.81 156.99 147.4 160.02Q143.99 163.04 143.22 168.42Z" />
      <path d="M240.95 168.8V200H227.51V170.62Q227.51 164.29 224.34 160.88Q221.18 157.47 215.7 157.47Q210.14 157.47 206.92 160.88Q203.7 164.29 203.7 170.62V200H190.26V146.82H203.7V153.44Q206.39 149.98 210.57 148.02Q214.74 146.05 219.74 146.05Q229.24 146.05 235.1 152.05Q240.95 158.05 240.95 168.8Z" />
      <path d="M267.44 157.86V183.58Q267.44 186.27 268.74 187.47Q270.04 188.67 273.11 188.67H279.35V200H270.9Q253.91 200 253.91 183.49V157.86H247.57V146.82H253.91V133.66H267.44V146.82H279.35V157.86Z" />
      <path d="M308.05 145.95Q313.24 145.95 317.94 148.21Q322.64 150.46 325.43 154.21V128.96H339.06V200H325.43V192.13Q322.93 196.06 318.42 198.46Q313.91 200.86 307.96 200.86Q301.24 200.86 295.67 197.41Q290.1 193.95 286.88 187.66Q283.67 181.38 283.67 173.22Q283.67 165.15 286.88 158.91Q290.1 152.67 295.67 149.31Q301.24 145.95 308.05 145.95ZM311.41 157.76Q307.67 157.76 304.5 159.58Q301.33 161.41 299.36 164.91Q297.4 168.42 297.4 173.22Q297.4 178.02 299.36 181.62Q301.33 185.22 304.55 187.14Q307.76 189.06 311.41 189.06Q315.16 189.06 318.42 187.18Q321.68 185.31 323.6 181.81Q325.52 178.3 325.52 173.41Q325.52 168.51 323.6 165.01Q321.68 161.5 318.42 159.63Q315.16 157.76 311.41 157.76Z" />
      <path d="M399.35 177.44H360.47Q360.95 183.2 364.5 186.46Q368.06 189.73 373.24 189.73Q380.73 189.73 383.9 183.3H398.39Q396.09 190.98 389.56 195.92Q383.03 200.86 373.53 200.86Q365.85 200.86 359.75 197.46Q353.66 194.05 350.25 187.81Q346.84 181.57 346.84 173.41Q346.84 165.15 350.2 158.91Q353.56 152.67 359.61 149.31Q365.66 145.95 373.53 145.95Q381.11 145.95 387.11 149.22Q393.11 152.48 396.42 158.48Q399.74 164.48 399.74 172.26Q399.74 175.14 399.35 177.44ZM385.82 168.42Q385.72 163.23 382.07 160.11Q378.42 156.99 373.14 156.99Q368.15 156.99 364.74 160.02Q361.34 163.04 360.57 168.42Z" />
      <path d="M430.46 145.95Q440.34 145.95 446.82 150.9Q453.3 155.84 455.51 164.77H441.02Q439.86 161.31 437.13 159.34Q434.39 157.38 430.36 157.38Q424.6 157.38 421.24 161.55Q417.88 165.73 417.88 173.41Q417.88 180.99 421.24 185.17Q424.6 189.34 430.36 189.34Q438.52 189.34 441.02 182.05H455.51Q453.3 190.69 446.78 195.78Q440.25 200.86 430.46 200.86Q422.78 200.86 416.82 197.46Q410.87 194.05 407.51 187.86Q404.15 181.66 404.15 173.41Q404.15 165.15 407.51 158.96Q410.87 152.77 416.82 149.36Q422.78 145.95 430.46 145.95Z" />
      <path d="M494.97 200 476.92 177.34V200H463.48V128.96H476.92V169.38L494.78 146.82H512.25L488.83 173.5L512.44 200Z" />
    </svg>
  )
}

function DeckLockup() {
  return (
    <svg className="ad-card ad-card--lockup" viewBox="246 145 832 933" fill="none" role="img" aria-label="AgentDeck">
      <rect className="ad-card__counter" x="330" y="474" width="600" height="520" />
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
      <svg className="ad-tree__lines" viewBox="0 0 950 380" preserveAspectRatio="none">
        <path className="edge edge-1" pathLength="1" d="M0 190 H150" />
        <path className="edge edge-2" pathLength="1" d="M250 190 H360" />
        <path className="edge edge-3" pathLength="1" d="M520 190 H550" />
        <path className="edge edge-4" pathLength="1" d="M550 190 V84 L564 70 H590" />
        <path className="edge edge-5" pathLength="1" d="M550 190 H590" />
        <path className="edge edge-6" pathLength="1" d="M550 190 V296 L564 310 H590" />
        <path className="edge edge-7" pathLength="1" d="M750 190 H790" />
      </svg>

      <div className="tree-node tree-node--run is-root">
        <span className="tree-node__name">Run</span>
      </div>

      {([
        ["answer-wf", "workflow", "answer", "Workflow"],
        ["docs", "tool", "search_docs", "Tool"],
        ["researcher", "agent", "researcher", "Agent"],
        ["jack", "agent", "Jack", "Agent"],
        ["inspect", "tool", "inspect_code", "Tool"],
      ] as const).map(([slot, kind, invocation, label]) => (
        <div key={slot} className={`tree-node tree-node--${slot}`}>
          <span className="tree-node__icon">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              {ICONS[kind]}
            </svg>
          </span>
          <span className="tree-node__text">
            <span className="tree-node__name">{invocation}</span>
            <span className="tree-node__kind">{label}</span>
          </span>
        </div>
      ))}
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

  return (
    <section className="ad-hero">
      <div className="ad-hero__backdrop" aria-hidden="true" />

      <div className="ad-hero__content">
        <div className="ad-hero__eyebrow">
          <DeckMark active />
          <Wordmark />
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

        <div className={`ad-stage phase-${phase}`} aria-label="Agents, tools, workflows and skills assembling into a Deck, which is activated and runs as one execution tree">
          <div className="ad-stage__grid" aria-hidden="true" />

          <div className="ad-stage__modules">
            {modules.map((item) => (
              <ModuleCard key={item.id} item={item} phase={phase} />
            ))}
          </div>

          <div className={`ad-deck ${showDeck ? "is-visible" : ""} ${activeDeck ? "is-active" : ""}`}>
            <div className="ad-deck__surface">
              <DeckLockup />
            </div>
          </div>

          <ExecutionTree active={showTree} />

        </div>

      </div>
    </section>
  );
}
