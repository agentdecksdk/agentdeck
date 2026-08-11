# Plan — #219, Ask AgentDeck: the reference application

**Status:** proposed · **Date:** 2026-08-11 · **Baseline:** `dev` at `a20f526`, package
`3.0.0b1`, gate green (`1217 passed, 99 skipped`, contracts 11/11).

The last thing v3 does before the stable tag. #219 is not a feature — it is the **release-level
validation** that the surface about to be frozen is sufficient for a real application. The
assistant is the pretext; the finding list is the deliverable.

> AgentDeck should be able to build the agent that teaches developers how to use AgentDeck.

This plan is written against the tree, not against the issue text. Everything in §1 was verified
by reading the code today; where the issue assumes something the tree does not offer, §2 rules on
it rather than leaving it for whoever picks this up to discover halfway through.

---

## 1. What the tree actually offers

Seven facts that decide the design. Each one is checked, not remembered.

| # | Fact | Where |
|---|---|---|
| 1 | The docs corpus is **117 KB across 22 `.mdx` pages** — roughly 30k tokens, the whole thing | `docs-site/content/` |
| 2 | The site is **Nextra 4 / Next.js 16, statically exported** to `docs-site/out`, and Pages deploys **only on `release: published`** | `docs-site/package.json`, `.github/workflows/docs-pages.yml` |
| 3 | **Pagefind already indexes the site** for human search — but into binary shards under `out/_pagefind`, built from the *rendered* site, consumed in the browser | `package.json` `postbuild` |
| 4 | The packaged HTTP surface's chat body is exactly `{"session_id", "message"}`, and **the v1 wire is frozen byte-for-byte** by `tests/golden/` | `agentdeck/serve.py:17`, `tests/golden/` |
| 5 | **A context cannot cross the HTTP surface.** A run started through `asgi()` carries `context=None`, always | `reference/deck.mdx` → *Where a context does not reach* |
| 6 | `examples/*/` get build-level CI coverage for free, from a list with a **hardcoded expected set** | `tests/test_examples.py:34` |
| 7 | `tests/test_docs_examples.py` already runs a **scripted OpenAI-compatible HTTP server** so fences execute offline and deterministically | `tests/test_docs_examples.py:66` |

## 2. Rulings

### Ruling 1 — No retrieval infrastructure. The corpus fits in a prompt.

**Two tools over `Path.read_text()`, and a page index in the instructions.** `search_docs(query)`
scans 22 files; `read_doc(slug)` returns one. No embeddings, no vector store, no chunking, no
new dependency, no index to rebuild when a page changes.

Fact 1 is why. At 117 KB the retrieval problem does not exist yet — a substring scan over 22
files is faster than an embedding round-trip and cannot return a stale chunk. Fact 3 is why
Pagefind is not reused: its index is built from the rendered site and read by a browser, so
reaching it from Python means parsing binary shards to get back text that is sitting in
`docs-site/content/` as plain files.

This is also the better demonstration. A reader inspecting the reference app should meet two
ordinary Python functions in `tools=`, not a retrieval stack that teaches them nothing about
AgentDeck. **Upgrade trigger:** the corpus passing roughly 500 KB, or a measured miss rate the
scan cannot fix — at which point the tool signature does not change, only its body.

### Ruling 2 — Page context is *data in the input*; `Context[T]` carries the corpus.

The issue asks for both "current page context" and "runtime context," and it is tempting to make
them the same mechanism. They are not, and fact 5 is why.

- **The current page, section and selection come from a browser.** They cross HTTP. A context
  structurally cannot. They travel as **data in the run's input** — a small structured preamble
  the agent reads, alongside the user's question.
- **The docs corpus is a live object the server owns** — a resolved content path plus the parsed
  page index, built once at startup. That is exactly the "database handle, a client" shape
  `Context[T]` exists for, and it is what `read_doc`/`search_docs` declare.

So the app declares `Deck(agents=[...], context=DocsCorpus)`, gets the build-time check over
every `Context[...]` in the catalog, and passes the instance per run. Nothing decorative: remove
the context and the tools have no corpus to read.

### Ruling 3 — The app writes its own HTTP route over `deck.stream()`, not `asgi()`.

Follows from ruling 2 and facts 4 and 5, and it is the single most load-bearing ruling here.

`asgi()` cannot serve this application. A run through it carries no context (fact 5), so the
tools would find `ctx.data is None`; and its body has no field for page context (fact 4), which
is frozen and **must not be extended by this issue**. A change to the v1 wire is a `tests/golden/`
change, and #219 is not a schema PR.

The reference app therefore owns ~40 lines of FastAPI calling `deck.stream(name, input,
context=corpus, session_id=...)` and rendering events to SSE. That is not a workaround — it is
what an embedded application *is*, and it exercises the public surface more honestly than
mounting a prebuilt server would. **Both consequences go in the friction ledger** (§4): whether
`asgi()` is sufficient for a real embedded app is precisely the question #219's last done-when
asks, and the answer this plan predicts is *no, and that is fine, provided it is documented*.

### Ruling 4 — Two eval layers. Only the model-free one runs in CI.

- **Layer A, deterministic, in CI.** For each question, assert `search_docs` surfaces the page
  that answers it, and that the deck builds with its tools compiled. No model, no network, no
  flake. This is a retrieval regression test, and retrieval is what actually rots when a page is
  renamed.
- **Layer B, model-graded, on demand.** `make eval-docs-agent` runs the real questions against a
  real model and reports. Not in CI: a model-graded suite in a required check is a flaky gate,
  and #219 asks for CI "at least at build/integration-test level," which layer A plus fact 7's
  scripted server satisfies.

Fact 7 is reused rather than reinvented — the scripted Chat-Completions server already exists and
already proves an end-to-end turn offline.

### Ruling 5 — Configuration in, secrets out.

The static bundle reads `NEXT_PUBLIC_AGENTDECK_API_URL` at **build** time, defaulting to
`http://localhost:8100` so a contributor running `npm run dev` gets a working panel with no
setup. The Pages build (fact 2, on release publish) sets it to the Cloudflare Tunnel hostname
from a repository variable. `OPENAI_API_KEY` lives only in the backend process — the bundle is
public and always will be. A missing or unreachable API URL degrades the panel to a disabled
state with a one-line explanation, never a broken page.

**`agentdecksdk.com` is not part of this plan.** The domain is real and is the right long-term
home, but pointing the site at it touches the README's absolute links, the docs frontmatter, the
Pages configuration and `test_docs_site_links_in_repo_markdown_reach_a_real_page` — which is
hardcoded to `sagi5060.github.io`. That belongs to #140, and doing it here would mean re-auditing
the docs this milestone just audited. The API URL being a build-time variable means the switch
costs one variable change whenever #140 happens.

### Ruling 6 — It lives in `examples/`.

`examples/ask-agentdeck/` for the backend, a component under `docs-site/` for the panel. Fact 6
gives the backend build-level CI coverage for the price of one line in
`tests/test_examples.py:34`, and it puts the reference app exactly where a reader already looks
for copyable code. No new top-level directory: "official reference application" is a claim about
quality, not about needing its own tree.

---

## 3. Slices

Four, each landing on the same PR to `dev`, each independently reviewable. The ledger (§4) is
appended to as they land, not written at the end.

### Slice 1 — The agent, headless

`examples/ask-agentdeck/.agentdeck/agents/ask/agent.py`, the `DocsCorpus` context type, the two
tools, and `run.py "how do I create an agent?"`. No HTTP, no UI.

**Done when:** `python run.py "<question>"` answers from the corpus and cites the pages it read;
`Deck(context=DocsCorpus)` build-time validation passes over both tools; a question the corpus
cannot answer gets *"the docs do not cover this"* rather than an invented API.

The grounding rule is an instruction plus a tool contract, not a hope: the agent may only name an
AgentDeck symbol it has read in a tool result during this turn. Layer B (ruling 4) is what
measures whether that holds.

### Slice 2 — The route

The ~40-line FastAPI app over `deck.stream()` (ruling 3), the page-context input contract
(ruling 2), SSE out, `session_id` for multi-turn.

**Done when:** `uvicorn` serves it; a `curl` with a page context gets a streamed answer whose
content depends on the page supplied; the context reaches both tools on a served run — the thing
`asgi()` cannot do, demonstrated working.

### Slice 3 — The panel

The docs-site component: an **Ask AgentDeck** affordance on every page, sending the current page,
section, and selected text with the question. Build-time API URL, graceful degradation
(ruling 5).

**Done when:** `npm run build` produces a static bundle containing no secret and no hardcoded
host; the panel works against `localhost` in `npm run dev`; asking *"explain the code on this
page"* with nothing selected still works.

### Slice 4 — Evals and CI

Layer A wired into the suite, layer B behind a make target, the `tests/test_examples.py:34`
list updated, the tunnel documented (ruling 5).

**Done when:** `make check` covers layer A and stays offline; `make eval-docs-agent` runs layer B
against a real model; the ten question categories #219 lists each have at least one case.

---

## 4. The friction ledger — the actual deliverable

Kept in the PR body and folded into the pre-release findings report. One row per thing that was
awkward, with the discriminating question: **is this the SDK's fault, and does it block v3?**

Three entries are predicted before a line is written, and being wrong about them is itself a
result worth recording:

| Predicted | The question it forces |
|---|---|
| `asgi()` cannot serve an app that needs a context (fact 5) | Is the packaged surface for demos only? If a real embedded app always writes its own route, the docs should say so plainly — or `asgi()` should grow a context factory (v3.1) |
| The frozen chat body has no room for per-run metadata (fact 4) | Page context works fine as input data. But every embedded app will want a structured side channel; is that a v3.1 wire addition, and does #156's minor-bump machinery already cover it? |
| One deck per process (#204) | The backend is one process with one deck, so this costs nothing here. Confirming that is worth as much as finding a problem |

Anything the ledger records is **fixed before the tag only if it makes the reference app
impossible or the docs false.** Everything else is filed against `v3.1 — batteries` with the
reproduction attached. This plan does not get to redesign the surface it exists to validate.

---

## 5. Mapping to the issue's done-when list

| #219 asks | Where |
|---|---|
| Assistant integrated into the docs site | Slice 3 |
| Built against the v3 public surface, not a private runtime | Slices 1–2; ruling 3 is what makes it true |
| Answers representative SDK questions from authoritative sources | Slice 1; ruling 1 |
| Current-page context can be supplied | Slice 2–3; ruling 2 |
| Responses stream through the normal runtime/event path | Slice 2 — `deck.stream()`, canonical events |
| Small and clear enough to be a reference implementation | Rulings 1 and 6 |
| A small SDK-focused eval/regression set | Slice 4; ruling 4 |
| Exercised in CI at build/integration level | Slice 4, layer A; fact 6 |
| Frontend reads the API URL from configuration | Ruling 5 |
| Local development works against `localhost` | Ruling 5 — the default |
| Pages deployment reaches the agent through the tunnel | Ruling 5; fact 2 |
| Backend secrets never in the static bundle | Ruling 5 |
| Friction fixed before v3 or captured as follow-up | §4 |
| Demonstrates the frozen surface is sufficient | §4 — and the honest answer may be *"sufficient, with `asgi()`'s limits documented"* |

## 6. What this plan does not do

- **No versioned documentation.** #219 asks the design to *remain correct* when versioned docs
  arrive at the v2→v3 boundary; it does not ask for them. `read_doc(slug)` gains a version
  argument when there is a second version to read. Building it now is scaffolding for later.
- **No permanent hosting.** The issue says so explicitly: local machine plus tunnel is the
  deployment strategy, and availability depending on a laptop is accepted.
- **No new event kinds, no wire change, no golden regeneration.** If a slice appears to need one,
  it stops and the need goes in the ledger.
- **No coupling to #140.** The panel is a component with its own styling; a Docusaurus migration
  re-hosts it rather than rewriting it. Ruling 5 keeps the domain switch to one variable.
