/** The mental model, drawn: four declarations feed one Deck, a Deck starts a Run, a Run is what
 *  Control and Events attach to. Text is real `<text>` rather than a raster, so it inherits the
 *  page's colour and scales with the article. */
export function MentalModel() {
  return (
    <svg
      className="agentdeck-mental-model"
      viewBox="0 0 640 182"
      role="img"
      aria-label="Agent, Tool, Workflow and Skill compose into a Deck; a Deck starts a Run; Control and Events attach to a Run."
    >
      {DECLARATIONS.map(({ label, x }) => (
        <g key={label}>
          <rect className="md-box" x={x} y={6} width={118} height={24} rx={5} />
          <text className="md-label" x={x + 59} y={22}>
            {label}
          </text>
          <path className="md-line" d={`M ${x + 59} 30 V 44`} />
        </g>
      ))}

      <path className="md-line" d="M 113 44 H 527" />
      <path className="md-line" d="M 320 44 V 54" />
      <Head y={58} />

      <rect className="md-box md-box--deck" x={254} y={58} width={132} height={30} rx={6} />
      <text className="md-label md-label--strong" x={320} y={77}>
        Deck
      </text>

      <path className="md-line" d="M 320 88 V 100" />
      <Head y={104} />

      <rect className="md-box md-box--run" x={254} y={104} width={132} height={30} rx={6} />
      <text className="md-label md-label--strong" x={320} y={123}>
        Run
      </text>

      <path className="md-line" d="M 320 134 L 196 152" />
      <path className="md-line" d="M 320 134 L 444 152" />

      <rect className="md-box" x={137} y={152} width={118} height={24} rx={5} />
      <text className="md-label" x={196} y={168}>
        Control
      </text>

      <rect className="md-box" x={385} y={152} width={118} height={24} rx={5} />
      <text className="md-label" x={444} y={168}>
        Events
      </text>
    </svg>
  )
}

const DECLARATIONS = [
  { label: 'Agent', x: 54 },
  { label: 'Tool', x: 192 },
  { label: 'Workflow', x: 330 },
  { label: 'Skill', x: 468 },
]

function Head({ y }: { y: number }) {
  return <path className="md-head" d={`M 315 ${y - 5} L 325 ${y - 5} L 320 ${y} Z`} />
}
