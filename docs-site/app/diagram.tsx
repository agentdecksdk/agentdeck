import React from 'react'

export function MentalModel() {
  return (
    <div className="agentdeck-mental-model">
      <div className="model-row inputs">
        <span className="node">Agents</span>
        <span className="plus">+</span>
        <span className="node">Tools</span>
        <span className="plus">+</span>
        <span className="node">Workflows</span>
        <span className="plus">+</span>
        <span className="node">Skills</span>
      </div>
      <div className="arrow-down">↓</div>
      <div className="model-row hub">
        <span className="node primary">Deck</span>
      </div>
      <div className="arrow-down">↓</div>
      <div className="model-row runtime">
        <span className="node secondary">Run</span>
      </div>
      <div className="split-arrows">
        <span className="arrow-left">↙</span>
        <span className="arrow-right">↘</span>
      </div>
      <div className="model-row outputs">
        <span className="node output">Control</span>
        <span className="node output">Events</span>
      </div>
    </div>
  )
}
