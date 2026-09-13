// Drives the official @ag-ui/client against a running AGUI.http() endpoint and prints the
// event-type lifecycle as JSON, for tests/bindings/test_agui_client_conformance.py.
"use strict";

const { HttpAgent } = require("@ag-ui/client");

async function drive(agent, params) {
  const types = [];
  let outcome = null;
  await agent.runAgent(params, {
    onEvent({ event }) {
      types.push(event.type);
    },
    onRunFinishedEvent({ outcome: seen }) {
      outcome = seen;
    },
  });
  return { types, outcome };
}

async function runText(url) {
  const agent = new HttpAgent({
    url,
    threadId: `js-text-${Date.now()}`,
    initialMessages: [{ id: "m1", role: "user", content: "hi" }],
  });
  const run = await drive(agent, { forwardedProps: { agentdeck: { target: "Greeter" } } });
  return { scenario: "text", runs: [run] };
}

async function runHitl(url) {
  const agent = new HttpAgent({
    url,
    threadId: `js-hitl-${Date.now()}`,
    initialMessages: [{ id: "m1", role: "user", content: "kites" }],
  });
  const first = await drive(agent, { forwardedProps: { agentdeck: { target: "Survey" } } });
  if (first.outcome !== "interrupt" || agent.pendingInterrupts.length !== 1) {
    throw new Error(`expected exactly one pending interrupt, got outcome=${first.outcome}`);
  }
  const interruptId = agent.pendingInterrupts[0].id;
  const resumed = await drive(agent, { resume: [{ interruptId, status: "resolved", payload: "red" }] });
  return { scenario: "hitl", runs: [first, resumed] };
}

async function main() {
  const [, , url, scenario] = process.argv;
  if (!url || !["text", "hitl"].includes(scenario)) {
    throw new Error("usage: node run_scenario.js <url> <text|hitl>");
  }
  const result = scenario === "text" ? await runText(url) : await runHitl(url);
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exitCode = 1;
});
