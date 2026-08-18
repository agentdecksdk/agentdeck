# Plan  -  #219, Ask AgentDeck: the reference application

**Status:** proposed · **Date:** 2026-08-11 · **Baseline:** `dev` at `a20f526`, package
`3.0.0b1`, gate green (`1217 passed, 99 skipped`, contracts 11/11).

The last thing v3 does before the stable tag. #219 is release-level validation that the surface
about to be frozen is sufficient for a real application: the assistant is the pretext, the
friction ledger (§4) is the deliverable.

> AgentDeck should be able to build the agent that teaches developers how to use AgentDeck.

## 1. What the tree actually offers

| # | Fact | Where |
|---|---|---|
| 1 | The docs corpus is **117 KB across 22 `.mdx` pages**  -  roughly 30k tokens, the whole thing | `docs-site/content/` |
| 2 | The site is **Nextra 4 / Next.js 16, statically exported** to `docs-site/out`, and Pages deploys **only on `release: published`** | `docs-site/package.json`, `.github/workflows/docs-pages.yml` |
| 3 | **Pagefind already indexes the site** for human search  -  but into binary shards under `out/_pagefind`, built from the *rendered* site, consumed in the browser | `package.json` `postbuild` |
| 4 | The packaged HTTP surface's chat body is exactly `{"session_id", "message"}`, and **the v1 wire is frozen byte-for-byte** by `tests/golden/` | `agentdeck/serve.py:17`, `tests/golden/` |
| 5 | **A context cannot cross the HTTP surface.** A run started through `asgi()` carries `context=None`, always | `reference/deck.mdx` → *Where a context does not reach* |
| 6 | `examples/*/` get build-level CI coverage for free, from a list with a **hardcoded expected set** | `tests/test_examples.py:34` |
| 7 | `tests/test_docs_examples.py` already runs a **scripted OpenAI-compatible HTTP server** so fences execute offline and deterministically | `tests/test_docs_examples.py:66` |

## 2. Rulings

**Ruling 1  -  No retrieval infrastructure; the corpus fits in a prompt.** `search_docs(query)`
substring-scans the 22 files, `read_doc(slug)` returns one, the page index lives in the
instructions  -  no embeddings, no vector store, no new dependency. Pagefind is not reused because
its index is built from the rendered site and read by a browser, so reaching it from Python means
parsing binary shards for text that sits in `docs-site/content/` as plain files. **Upgrade
trigger:** the corpus passing roughly 500 KB, or a measured miss rate the scan cannot fix  -  the
tool signature does not change, only its body.

**Ruling 2  -  Page context is *data in the input*; `Context[T]` carries the corpus.** The current
page, section and selection come from a browser and must cross HTTP, which a context structurally
cannot (fact 5), so they travel as a small structured preamble in the run's input. The corpus  -  a
resolved content path plus the parsed page index, built once at startup  -  is the database-handle
shape `Context[T]` exists for: `Deck(agents=[...], context=DocsCorpus)`, checked at build time,
instance passed per run.

**Ruling 3  -  The app writes its own HTTP route over `deck.stream()`, not `asgi()`.** `asgi()`
cannot serve this application: a run through it carries no context (fact 5) so the tools would find
`ctx.data is None`, and its body has no field for page context (fact 4), which is frozen and must
not be extended by this issue. So the app owns ~40 lines of FastAPI calling `deck.stream(name,
input, context=corpus, session_id=...)` and renders events to SSE  -  both consequences go in §4.

**Ruling 4  -  Two eval layers; only the model-free one runs in CI.** Layer A, deterministic and in
CI: per question, assert `search_docs` surfaces the page that answers it and that the deck builds
with its tools compiled  -  retrieval is what rots when a page is renamed. Layer B, model-graded and
on demand: `make eval-docs-agent` runs the real questions against a real model, out of CI because a
model-graded required check is a flaky gate. Fact 7's scripted server gives the offline
end-to-end turn.

**Ruling 5  -  Configuration in, secrets out.** The bundle reads `NEXT_PUBLIC_AGENTDECK_API_URL` at
**build** time, defaulting to `http://localhost:8100` so `npm run dev` works with no setup; the
Pages build (fact 2) sets it to the Cloudflare Tunnel hostname from a repository variable.
`OPENAI_API_KEY` lives only in the backend process, and a missing or unreachable API URL degrades
the panel to a disabled state with a one-line explanation rather than a broken page.
**`agentdecksdk.com` is not part of this plan**  -  it belongs to #140 and touches the README's
absolute links, the docs frontmatter, the Pages configuration and
`test_docs_site_links_in_repo_markdown_reach_a_real_page` (hardcoded to `sagi5060.github.io`); the
build-time variable keeps that switch to one variable change.

**Ruling 6  -  It lives in `examples/`.** `examples/ask-agentdeck/` for the backend, a component
under `docs-site/` for the panel  -  where a reader already looks for copyable code, and no new
top-level directory.

> **Amended in slice 1, 2026-08-11.** This ruling assumed a `.agentdeck/` project picked up by
> `tests/test_examples.py`'s glob at `:34`. It cannot be one  -  that is the slice's first finding
> (§4). The app composes explicitly, `Deck(agents=[ask], context=DocsCorpus)`, and brings its own
> `tests/test_ask_agentdeck.py`, which slice 4 grows into eval layer A. The directory is
> unchanged; the glob simply does not match it, and `tests/test_examples.py:34`'s hardcoded list
> stays as it is.

## 3. Slices

Four, all landing on the same PR to `dev`, each independently reviewable. The ledger (§4) is
appended to as they land.

| # | Deliverable | Done when |
|---|---|---|
| 1 | The agent, headless: `examples/ask-agentdeck/.agentdeck/agents/ask/agent.py`, `DocsCorpus`, the two tools, `run.py "<question>"`. No HTTP, no UI | `run.py` answers from the corpus and cites the pages it read; `Deck(context=DocsCorpus)` validation passes over both tools; an uncovered question gets *"the docs do not cover this"* rather than an invented API |
| 2 | The route: the ~40-line FastAPI app over `deck.stream()` (ruling 3), the page-context input contract (ruling 2), SSE out, `session_id` for multi-turn | `uvicorn` serves it; a `curl` with a page context gets a streamed answer whose content depends on the page supplied; the context reaches both tools on a served run  -  the thing `asgi()` cannot do |
| 3 | The panel: an **Ask AgentDeck** affordance on every docs page, sending current page, section and selected text with the question; build-time API URL, graceful degradation (ruling 5) | `npm run build` produces a bundle with no secret and no hardcoded host; the panel works against `localhost` in `npm run dev`; *"explain the code on this page"* with nothing selected still works |
| 4 | Evals and CI: layer A in the suite, layer B behind a make target, `tests/test_examples.py:34` updated, the tunnel documented | `make check` covers layer A and stays offline; `make eval-docs-agent` runs layer B against a real model; each of #219's ten question categories has at least one case |

The grounding rule is an instruction plus a tool contract, not a hope: the agent may only name an
AgentDeck symbol it has read in a tool result during this turn. Layer B measures whether it holds.

## 4. The friction ledger  -  the actual deliverable

Kept in the PR body and folded into the pre-release findings report. One row per thing that was
awkward, with the discriminating question: **is this the SDK's fault, and does it block v3?**

**Found in slice 1  -  a bundle cannot share a type with the program that composes it.** A
`.agentdeck/` bundle importing a module beside the project dir works only when the process was
started from that directory; from anywhere else `Deck.from_project()` raises `ConfigError:
agents/x/agent.py failed to import: No module named 'shared'`. Putting the module *inside*
`.agentdeck/` does resolve  -  the project dir is mounted as a package  -  but the package name is
`registry._PROJECT_ALIAS`, internal, and does not exist until `from_project()` has run, so the host
cannot import from it at module level. **Consequence:** any project whose bundles and host share a
type must use `Deck(agents=[...])`. **Verdict: does not block v3.** Worth a v3.1 issue on whether
`.agentdeck/` should have a supported shared-module convention, and a docs line, since nothing
warns anyone first.

**Found in slice 2  -  `DataBlock` cannot be sent *to* a model, so structured input has no typed
form.** Ruling 2's page context is exactly what `DataBlock` looks like it exists for, and it
raises:

```
ConfigError: openai-agents engine cannot send a 'data' block to the model;
it accepts text, image, and audio (chat-completions only) input blocks
```

`DataBlock` is an *output* block in practice. **Consequence:** every embedded application that
wants to hand the model structured context invents its own prose preamble, and no two will agree;
this one's is `<context>…</context>`, in `server.py:page_context_input`. **Verdict: does not block
v3**  -  a page slug is something the model reads. Worth a v3.1 issue: either the engine renders a
`DataBlock` as JSON text rather than refusing it, or the block table says plainly which types are
input-capable. `reference/deck.mdx` today lists all five under *What `input` accepts* and
qualifies it a paragraph later.

**Confirmed in slice 2  -  ruling 3 holds, and it works.** `tests/test_ask_agentdeck_server.py`
scripts a model that calls `read_doc`, and the tool answers out of `docs.data.pages` on a served
run  -  impossible by construction through `Deck.asgi()`. Forty lines of FastAPI is the whole cost.

| Predicted | The question it forces |
|---|---|
| ~~`asgi()` cannot serve an app that needs a context~~  -  **confirmed, slice 2** | Is the packaged surface for demos only? A real embedded app writes its own route, and the docs should say so. v3.1: should `asgi()` take a context factory? |
| ~~The frozen chat body has no room for per-run metadata~~  -  **confirmed, slice 2**, and sharper: the *content model* has no room either, since `DataBlock` is refused on input | Is a structured per-run metadata channel a v3.1 wire addition, and does #156's minor-bump machinery already cover it? |
| One deck per process (#204) | The backend is one process with one deck, so this costs nothing here. Confirming that is worth as much as finding a problem |

Anything the ledger records is **fixed before the tag only if it makes the reference app
impossible or the docs false.** Everything else is filed against `v3.1  -  batteries` with the
reproduction attached.

## 5. Mapping to the issue's done-when list

| #219 asks | Where |
|---|---|
| Assistant integrated into the docs site | Slice 3 |
| Built against the v3 public surface, not a private runtime | Slices 1–2; ruling 3 is what makes it true |
| Answers representative SDK questions from authoritative sources | Slice 1; ruling 1 |
| Current-page context can be supplied | Slice 2–3; ruling 2 |
| Responses stream through the normal runtime/event path | Slice 2  -  `deck.stream()`, canonical events |
| Small and clear enough to be a reference implementation | Rulings 1 and 6 |
| A small SDK-focused eval/regression set | Slice 4; ruling 4 |
| Exercised in CI at build/integration level | Slice 4, layer A; fact 6 |
| Frontend reads the API URL from configuration | Ruling 5 |
| Local development works against `localhost` | Ruling 5  -  the default |
| Pages deployment reaches the agent through the tunnel | Ruling 5; fact 2 |
| Backend secrets never in the static bundle | Ruling 5 |
| Friction fixed before v3 or captured as follow-up | §4 |
| Demonstrates the frozen surface is sufficient | §4  -  and the honest answer may be *"sufficient, with `asgi()`'s limits documented"* |

## 6. What this plan does not do

| Not doing | Why |
|---|---|
| Versioned documentation | #219 asks the design to *remain correct* when versioned docs arrive, not for them. `read_doc(slug)` gains a version argument when there is a second version to read |
| Permanent hosting | The issue says so: local machine plus tunnel, availability depending on a laptop accepted |
| New event kinds, wire changes, golden regeneration | If a slice appears to need one, it stops and the need goes in the ledger |
| Coupling to #140 | The panel is a component with its own styling; a Docusaurus migration re-hosts it. Ruling 5 keeps the domain switch to one variable |
