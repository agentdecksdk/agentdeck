# Plan — the content model, end to end

**Status:** proposed · **Date:** 2026-08-10 · **Baseline:** `dev` at `e34f9f2`
Gates #159 (`AudioBlock`) and #161 (multimodal input). Ordered against #156 (envelope versioning).
Written because the maintainer ruled multimodal gets a design pass before either is implemented —
so `AudioBlock` is not bolted on and #161 does not discover the gaps.

## Where we actually are

Verified against the tree, not the issues.

`agentdeck/core/content.py` is 114 lines and already holds four content atoms plus a
forward-compatibility escape:

| block | shape | who produces it today |
|---|---|---|
| `TextBlock` | `text: str` | everything |
| `ImageBlock` | `media_type`, `data_b64` — **bytes inline** | nothing; accepted, never consumed |
| `ResourceBlock` | `uri`, `media_type` — **bytes elsewhere** | nothing; accepted, never consumed |
| `DataBlock` | `data: JsonData` | the langgraph engine, both directions |
| `UnknownBlock` | `type`, `raw_block` | the reader, for a kind it does not know |

Two things follow that most of this plan rests on.

**The union is closed at write and open at read.** `ContentBlock` is a `WrapValidator` that
falls back to `UnknownBlock` for an unrecognised `type`. Verified directly on today's code:

```python
>>> TypeAdapter(ContentBlock).validate_python(
...     {"type": "audio", "media_type": "audio/wav", "data_b64": "AAA="})
UnknownBlock(type='audio', raw_block={...})
```

So **adding a block kind does not break an existing reader** — it degrades to "a block I cannot
render", with the payload preserved. That is the property that makes everything below cheap.

**But `UnknownBlock` preserves without round-tripping.** The same value dumps as
`{"type": "audio", "raw_block": {"type": "audio", …}}` — the original shape is nested, not
restored. Anything that reads events and re-emits them (a relay, a protocol adapter, a
re-indexer) would corrupt an unknown block rather than pass it through. Nothing does that today.
It becomes real the moment #129's protocol adapters exist, and it is a bug then, not now.

**Only text reaches a model.** `_to_sdk_input` (`adapters/engines/openai_agents/engine.py:241`)
keeps `TextBlock` and raises on anything else, with a comment that says the reasoning plainly:
better to raise than answer a question the model never saw. The langgraph engine uses `DataBlock`
as graph state in and out, and ignores the rest.

So the gap is not "audio is missing". It is that **three of the five atoms are accepted by the
schema and consumed by nothing.**

## The six questions

### 1. What is the block set, and is it closed?

**Five kinds, plus the unknown fallback: `text`, `image`, `resource`, `data`, `audio`.**

`AudioBlock` mirrors `ImageBlock` exactly — `media_type`, `data_b64` — because the two have the
same problem (opaque bytes with a MIME type) and inventing a second shape for it would be
asymmetry with no payoff. `ResourceBlock` already covers by-reference for both.

Closed at write, open at read, which is what the code already does. No `video`, no `file`: add
one when something produces or consumes it, and the `UnknownBlock` path means a future reader
meets it gracefully.

### 2. What does an engine do with a block it cannot express?

**Raise, naming the block kind and the engine.** Never drop, never silently degrade.

The existing comment already argues this and is right: a dropped image means the model answered a
question it never saw, and the caller cannot tell from the response. A degraded run that looks
successful is worse than a failed one.

Concretely, `_to_sdk_input` grows from "keep text, raise on the rest" to a per-kind mapping, and
raises for a kind that engine genuinely cannot carry:

```
ConfigError: the openai-agents engine cannot send an 'audio' block to gpt-4.1-mini;
             this model accepts text and image. Use a text transcript, or route to a
             model that accepts audio.
```

**What this plan deliberately does not build:** a capability declaration per engine, checked at
`build()`. Input arrives at run time, not build time, so `build()` cannot know what blocks a
caller will send — a static declaration would be checking the wrong thing at the wrong moment.
If engines later need to advertise what they accept, that is a port change and its own effort.

### 3. Inbound and outbound — are they symmetric?

**No, and the plan should say so rather than imply parity.**

Inbound: image and audio become real. `_to_sdk_input` maps them to the SDK's multimodal input
parts, and raises where the target model cannot take them.

Outbound: **unchanged, and deferred.** An agent returns `TextBlock` (prose) or `DataBlock` (a
structured `output_type`); a workflow returns `DataBlock`. Nothing in the current SDK path
produces image or audio output, so a plan to carry it would be scaffolding for a caller that
does not exist. When a model does return audio, the block already exists and `RunCompleted.output`
is already `list[ContentBlock]` — that is additive, and this is the whole reason to define the
block set now rather than later.

State this asymmetry in the reference, or users will reasonably assume an agent can return audio
because the type allows it.

### 4. How does a block survive the log and the wire?

Unchanged, and this is the cheap part. Blocks are `CoreModel`s nested inside events
(`RunStarted.input`, `RunCompleted.output`), so they serialise with the envelope and land in
every store and on SSE with no per-store work. An old reader gets `UnknownBlock` — proven above.

Two consequences worth writing down:

- **Golden snapshots do not move** unless a fixture actually sends an image or audio. Adding the
  block kind changes no existing byte.
- **The re-emit hazard from §"Where we are" is a real future bug.** Before #129's protocol
  adapters relay events, `UnknownBlock` needs a round-trip that restores the original shape —
  either by dumping `raw_block` transparently, or by adapters refusing to relay a block they
  cannot round-trip. Worth its own issue now, while the reasoning is fresh, rather than being
  discovered by a corrupted relay later.

### 5. Binary payloads: inline or referenced?

**Both, with a documented threshold and an enforced ceiling.**

`ImageBlock`/`AudioBlock` carry inline base64; `ResourceBlock` carries a URI. The choice is the
caller's, but the guidance should not be left implicit, because the failure mode is nasty: an
event carrying a 10 MB base64 audio clip goes into the event log, into every store, and down
every SSE connection replaying that run — permanently, since the log is append-only.

- **Guidance:** inline for a few hundred KB — a snapshot, a short utterance. `ResourceBlock`
  above that.
- **Enforcement:** a hard cap on `data_b64` length, raising at construction with a message
  naming `ResourceBlock`. A limit that is only documented is a limit that ships violated.

The exact number is a judgement call — I would take **1 MB decoded** as the cap, being roughly a
minute of speech-quality audio or a large screenshot, and small enough that a run's log stays
manageable. Worth your ruling; it is easier to raise later than lower.

### 6. One schema change or two — and in what order?

**Two, and the roadmap has them backwards.** `docs/delivery/roadmap-v3.md` Wave 2 lists
#159 → #161 → #156. It should be **#156 first.**

They are independent in content — #156 restructures the envelope's `v`, audio adds a payload
kind — but not in sequence. #156 replaces the scalar `v: int` with major/minor semantics, where
**minor means "additive, compatible schema evolution"**. Adding a block kind is the textbook
minor bump: old readers keep parsing, they just meet `UnknownBlock`.

If audio lands first, it is an additive schema change with no way to signal itself, and the
version it should have bumped does not exist yet. If #156 lands first, audio becomes the first
real exercise of the minor-bump path — which is a far better test of that design than anything
synthetic, and it tells us immediately whether the semantics are usable.

So: **#156, then #159, then #161.**

## What this means for the issues

| issue | change |
|---|---|
| **#156** | moves ahead of #159/#161 in Wave 2 |
| **#159** | `AudioBlock` mirroring `ImageBlock`; plus the `data_b64` cap for both, which is arguably its own small fix |
| **#161** | `_to_sdk_input` becomes a per-kind mapping that raises with a message naming block and engine; inbound only |
| **new** | `UnknownBlock` does not round-trip — file before #129's adapters relay anything |
| **new** | inline-payload ceiling on `ImageBlock`/`AudioBlock` |

## Deliberately not in scope

- **Per-engine capability declaration.** Input is a run-time value; `build()` cannot check it.
- **Outbound image/audio.** Additive when a model produces it; scaffolding until then.
- **`video`/`file` kinds.** Add on first real consumer.
- **Transcoding or resizing.** agentdeck carries content; it does not process it.

## Open for a ruling

1. **The inline cap: 1 MB decoded?** Or a different number, or guidance only with no enforcement.
2. **Does `ImageBlock` become usable in the same slice as audio,** or does #161 do image first and
   audio follow? They share the code path, so one slice is cheaper — but it makes #161 bigger.
