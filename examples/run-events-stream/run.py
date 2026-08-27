import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager

from agentdeck import Agent, Deck
from agentdeck.core.content import TextBlock
from agentdeck.core.events import Event, MessageCompleted, RunCompleted, RunStarted, TextDelta, UsageReported
from agentdeck.testing import ScriptedModel, patch_model


@contextmanager
def model_configured() -> Iterator[None]:
    previous = os.environ.get("OPENAI_BASE_URL")
    os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OPENAI_BASE_URL", None)
        else:
            os.environ["OPENAI_BASE_URL"] = previous


def describe(event: Event) -> str:
    payload = event.payload
    if isinstance(payload, RunStarted):
        return f"{event.kind}: run_id={event.run_id} session_id={event.session_id}"
    if isinstance(payload, TextDelta):
        return f"{event.kind}: {payload.text!r}"
    if isinstance(payload, UsageReported):
        usage = payload.usage
        total_tokens = usage.input_tokens + usage.output_tokens
        return f"{event.kind}: total_tokens={total_tokens}"
    if isinstance(payload, MessageCompleted):
        return f"{event.kind}: text={payload.text!r}"
    if isinstance(payload, RunCompleted):
        text = "".join(block.text for block in payload.output if isinstance(block, TextBlock))
        return f"{event.kind}: output={text!r}"
    return event.kind


async def main() -> None:
    agent = Agent(name="Greeter", instructions="Answer with a short greeting.")
    deck = Deck(agents=[agent])

    with model_configured(), patch_model(ScriptedModel(deltas=("Hel", "lo"))):
        async with deck:
            async for event in deck.stream("Greeter", "Say hello", session_id="demo-session"):
                print(describe(event))


asyncio.run(main())
