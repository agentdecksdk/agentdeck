# Security Policy

## Reporting a vulnerability

Email **sagi.shabtai@outlook.com** with `agentdeck security` in the subject. Please do not
open a public issue for a suspected vulnerability  -  a public issue is the disclosure.

Include what you need us to reproduce it: the version (`python -c "import agentdeck;
print(agentdeck.__version__)"`), the configuration that matters (which store backends, which
surfaces are exposed), and the smallest input that triggers it.

What to expect:

- **Acknowledgement within 5 working days.** This is a small project maintained by one person;
  if you have not heard back by then, assume the mail was lost and send it again.
- **An assessment within 10 working days**  -  whether it is in scope, how severe, and what the
  fix looks like.
- A fix released on the supported line, credited to you unless you would rather not be.

## Supported versions

The latest release and the `dev` branch. Fixes land on `dev` and go out in the next release;
there are no long-term support branches and no backports to older lines.

## Scope

**In scope**  -  anything where agentdeck itself is the weakness:

- Project discovery importing something it should not, or from somewhere it should not.
- A protocol binding (`deck.expose(...)`, e.g. `Native.http()`): a request reaching a run, a
  session, or an event log that belongs to another caller, or crossing a `namespace` boundary.
- The event log and the stores behind it: an injection into a backend query, or one run's
  events being readable as another's.
- Secrets escaping where they should not  -  an API key in a log line, an event payload, or an
  error message.
- Dependency handling in the packaged distribution.

**Out of scope**, and both of these are design, not oversight:

- **A model-chosen tool call is trusted by design.** agentdeck owns configuration; the OpenAI
  Agents SDK owns execution. When a model decides to call a tool you declared,
  that tool runs with the full privileges of the host process, with arguments the model chose.
  There is no allowlist, no confirmation step, and no privilege boundary between the model's
  decision and your function  -  so a prompt injection that reaches an agent reaches every tool
  that agent holds. Treat every tool you declare as reachable by untrusted input: keep the
  destructive ones behind a workflow with a human approval, not behind an agent's judgement.
- **Nothing is sandboxed.** Sandboxing is not part of v3  -  it was deferred, and the scaffolding
  for it was deleted rather than left half-built. Skills, tools, and workflows are ordinary
  Python running in your process, with your filesystem, your network, and your environment.
  Code you did not write and would not run yourself does not become safe by being loaded as a
  bundle.

Also out of scope: vulnerabilities in the OpenAI Agents SDK or any other
dependency (report those upstream; tell us too if agentdeck's use of them makes it worse), and
anything that requires an attacker to already control the machine or the `.agentdeck/`
directory.
