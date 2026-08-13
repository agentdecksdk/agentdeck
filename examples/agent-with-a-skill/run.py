import asyncio

from agentdeck import Deck


async def main() -> None:
    async with Deck.from_project() as deck:
        who = await deck.run("HandoverDesk", "who is on shift on 2026-03-03?", session_id="handover-demo")
        print(who.output)

        # The skill is what shapes this answer: the model loads it, then asks for whatever the note
        # is missing instead of inventing it.
        note = await deck.run(
            "HandoverDesk",
            "leave a note for the next shift: the card reader by the loading bay is offline again",
            session_id="handover-demo",
        )
        print(note.output)


asyncio.run(main())
