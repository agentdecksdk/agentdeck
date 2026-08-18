# Plan  -  the content model, end to end

**Status:** proposed · **Date:** 2026-08-10 · **Baseline:** `dev` at `e34f9f2`
Gates #159 (`AudioBlock`) and #161 (multimodal input). Ordered against #156 (envelope versioning).

## Where we are

`agentdeck/core/content.py` is 114 lines and already holds four content atoms plus a
forward-compatibility escape:

| block | shape | who produces it today |
|---|---|---|
| `TextBlock` | `text: str` | everything |
| `ImageBlock` | `media_type`, `data_b64`  -  **bytes inline** | nothing; accepted, never consumed |
| `ResourceBlock` | `uri`, `media_type`  -  **bytes elsewhere** | nothing; accepted, never consumed |
| `DataBlock` | `data: JsonData` | the langgraph engine, both directions |
| `UnknownBlock` | `type`, `raw_block` | the reader, for a kind it does not know |

**The union is closed at write and open at read.** `ContentBlock` is a `WrapValidator` that falls
back to `UnknownBlock` for an unrecognised `type`, verified on today's code:

```python
>>> TypeAdapter(ContentBlock).validate_python(
...     {"type": "audio", "media_type": "audio/wav", "data_b64": "AAA="})
UnknownBlock(type='audio', raw_block={...})
```

So **adding a block kind does not break an existing reader**  -  it degrades to "a block I cannot
render", payload preserved. That property is what makes everything below cheap.

**But `UnknownBlock` preserves without round-tripping.** The same value dumps as
`{"type": "audio", "raw_block": {"type": "audio", …}}`  -  the original shape is nested, not restored.
Anything that reads events and re-emits them (a relay, a protocol adapter, a re-indexer) would
corrupt an unknown block. Nothing does that today; it becomes real the moment #129's protocol
adapters exist, and it is a bug then, not now.

**Only text reaches a model.** `_to_sdk_input` (`adapters/engines/openai_agents/engine.py:241`)
keeps `TextBlock` and raises on anything else  -  better to raise than answer a question the model
never saw. The langgraph engine uses `DataBlock` as graph state in and out, and ignores the rest.

So the gap is not "audio is missing". It is that **three of the five atoms are accepted by the
schema and consumed by nothing.**

## The answers

**The block set is `text`, `image`, `resource`, `data`, `audio`, plus the unknown fallback  -  closed
at write, open at read.** `AudioBlock` mirrors `ImageBlock` exactly (`media_type`, `data_b64`)
because the two have the same problem, opaque bytes with a MIME type, and `ResourceBlock` already
covers by-reference for both. No `video`, no `file`: add one when something produces or consumes it.

**An engine that cannot express a block raises, naming the block kind and the engine**  -  never
drops, never silently degrades, because a degraded run that looks successful is worse than a failed
one. `_to_sdk_input` grows from "keep text, raise on the rest" into a per-kind mapping:

```
ConfigError: the openai-agents engine cannot send an 'audio' block to gpt-4.1-mini;
             this model accepts text and image. Use a text transcript, or route to a
             model that accepts audio.
```

**Inbound and outbound are not symmetric, and the reference must say so.** Inbound, image and audio
become real. Outbound is unchanged and deferred: an agent returns `TextBlock` or `DataBlock`, a
workflow returns `DataBlock`, and nothing in the SDK path produces image or audio output. When a
model does, the block already exists and `RunCompleted.output` is already `list[ContentBlock]`  -
additive, which is the whole reason to define the block set now.

**The log and the wire need no work.** Blocks are `CoreModel`s nested inside events
(`RunStarted.input`, `RunCompleted.output`), so they serialise with the envelope and land in every
store and on SSE for free; an old reader gets `UnknownBlock`. Two consequences: **golden snapshots
do not move** unless a fixture actually sends an image or audio, and the re-emit hazard above is a
real future bug  -  before #129's adapters relay events, `UnknownBlock` needs a round-trip that
restores the original shape, or adapters must refuse to relay a block they cannot round-trip. Worth
its own issue now rather than being discovered by a corrupted relay later.

**Binary payloads are both inline and referenced, with a documented threshold and an enforced ceiling
(ruling 1).** `ImageBlock`/`AudioBlock` carry inline base64, `ResourceBlock` carries a URI; the
choice is the caller's, and the guidance is inline for a few hundred KB  -  a snapshot, a short
utterance  -  `ResourceBlock` above that. The cap is enforced at construction because a limit that is
only documented is a limit that ships violated, and the failure mode is unbounded: a 10 MB base64
clip goes into the append-only event log, into every store, and down every SSE connection replaying
that run, permanently.

**This is two schema changes, and `roadmap-v3.md` Wave 2 has them backwards**  -  it lists #159 → #161
→ #156, and it should be **#156 first** (ruling 3). They are independent in content but not in
sequence: #156 replaces the scalar `v: int` with major/minor semantics where **minor means "additive,
compatible schema evolution"**, which is exactly what adding a block kind is.

## What this means for the issues

| issue | change |
|---|---|
| **#156** | moves ahead of #159/#161 in Wave 2 |
| **#159** | `AudioBlock` mirroring `ImageBlock`; plus the `data_b64` cap for both, which is arguably its own small fix |
| **#161** | `_to_sdk_input` becomes a per-kind mapping that raises with a message naming block and engine; inbound only |
| **new** | `UnknownBlock` does not round-trip  -  file before #129's adapters relay anything |
| **new** | inline-payload ceiling on `ImageBlock`/`AudioBlock` |

## Deliberately not in scope

- **Per-engine capability declaration**, checked at `build()`. Input arrives at run time, so
  `build()` cannot know what blocks a caller will send; a static declaration would check the wrong
  thing at the wrong moment. If engines later need to advertise what they accept, that is a port
  change and its own effort.
- **Outbound image/audio.** Additive when a model produces it; scaffolding until then.
- **`video`/`file` kinds.** Add on first real consumer.
- **Transcoding or resizing.** agentdeck carries content; it does not process it.

## Rulings taken (2026-08-10)

1. **The inline cap is 1 MB decoded**, enforced at construction on `ImageBlock` and `AudioBlock`,
   with the error naming `ResourceBlock` as the alternative. Roughly a minute of speech-quality
   audio or a large screenshot. Chosen low on purpose: raising a cap later is compatible, lowering
   one is not.
2. **Image and audio land in the same slice.** They share one code path in `_to_sdk_input`, so
   splitting them would mean writing and reviewing the per-kind mapping twice. #161 gets bigger;
   the total gets smaller.
3. **#156 lands before #159/#161.** Adding a block kind is the textbook "minor, additive" bump #156
   introduces semantics for, so audio becomes the first real exercise of that path rather than an
   additive change with no way to signal itself.

Wave 2's order in `docs/delivery/roadmap-v3.md` is updated to match.
