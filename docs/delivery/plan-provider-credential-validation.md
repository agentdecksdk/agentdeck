# Remove provider credential validation from the OpenAI Agents executor

## Context

Issue #519: `validate_model_requirements()` (`agentdeck/adapters/executors/openai_agents/runconfig.py:173-194`)
checks environment variables for four hardcoded prefixes (`anthropic`, `gemini`, `ollama`,
`openrouter`) and falls back to `OPENAI_API_KEY`/`OPENAI_BASE_URL` for everything else, `litellm`
and `any-llm` included. A `litellm/vertex_ai/...` model authenticated via Google ADC, with no API
key anywhere, fails `Deck.build()` demanding an OpenAI credential it will never use.

## Decision: remove the gate, don't widen it

Not "add `litellm`/`any-llm` to the exemption list". Remove `validate_model_requirements()`'s
credential checking entirely, for every prefix, hardcoded or not.

The problem is broader than two missing prefixes: AgentDeck is answering a question it doesn't
own. Model resolution and authentication belong to the wrapped Agents SDK and, beneath it, the
selected provider. Environment-variable presence isn't even authoritative for the four prefixes
already checked. `ANTHROPIC_API_KEY=invalid-value` passes today's check and still fails to
authenticate; an explicitly configured client can authenticate correctly with no env var at all
and still fails today's check. The validation produces false negatives without guaranteeing a true
positive.

Boundary, for the OpenAI Agents executor specifically:

    AgentDeck        owns: Deck construction, execution lifecycle, invocation, reporting
    Agents SDK       owns: model resolution, provider selection, provider-map behavior
    the provider     owns: credentials, provider-specific config, auth failures

`Deck.build()` means "this is a structurally valid AgentDeck configuration", not "every external
service this Deck may invoke is currently reachable and authenticated". A real auth failure surfaces
at the actual call, from the component that understands it: SDK resolves provider, provider
attempts the request, provider reports the real missing/invalid credential.

## Implementation

Remove the credential gate from `validate_model_requirements()`. If nothing AgentDeck-owned is
left to check, remove the function and its call site entirely, don't leave a stub.

**Routing is not authentication, don't touch it.** Whatever `provider_map` entries or other
configuration let `anthropic/...`, `gemini/...`, `openrouter/...` resolve through the Agents SDK
today keeps working exactly as it does now. This plan only removes "should `Deck.build()` reject
this", never "how does this model get routed".

**No new dependency.** Don't add `openai-agents[litellm]`/`[any-llm]` as this repo's own optional
extras. Nothing here needs to import either package: the check being removed never did either.

**Don't translate provider errors.** A real auth failure at execution time should still surface
the provider/SDK's own exception, not a rewritten AgentDeck message. Wrapping with call-site
context (e.g. "agent execution failed for model X") is fine if the original exception stays the
cause; replacing it with something generic like "model configuration is invalid" is not.

## Verification

| Check | How |
|---|---|
| `litellm/...` builds without `OPENAI_*` | `Deck(agents=[Agent(model="litellm/vertex_ai/gemini-1.5-pro")]).build()` succeeds, env clean |
| `any-llm/...` builds without `OPENAI_*` | same, for an `any-llm/...` model |
| The four previously-hardcoded prefixes build without their own env vars either | `anthropic/...`, `gemini/...`, `openrouter/...` build with no corresponding key set |
| Provider routing is unchanged | existing model-prefix/provider-map resolution tests still pass unmodified |
| A real auth failure still surfaces at execution, not build | invoke (SDK mocked at the appropriate boundary is fine) an unauthenticated provider, confirm the provider/SDK's own error reaches the caller, not a build-time rejection |
| AgentDeck-owned build validation is untouched | existing tests for AgentDeck's own config errors (duplicate registrations, unresolved references, unsupported combinations) still fail exactly as before |

`make check` in the foreground with `< /dev/null`, output pasted.

## Not in scope

Redesigning the Agents SDK's own provider map. Provider-specific auth diagnostics. Teaching
AgentDeck about individual litellm backends (Vertex ADC, AWS IAM, Azure). Changing how provider
packages get installed.
