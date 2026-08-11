"""Ask the docs agent one question, from the command line.

    python run.py "how do I create an agent?"

Composed explicitly — `Deck(agents=[...])`, not `Deck.from_project()`. Both are front doors onto
the same catalog, and this one is the right door here for a reason worth reading: the agent's
tools and the deck's context type are the *same* `DocsCorpus` class, and a `.agentdeck/` bundle
has nowhere to share a type with the program that composes it. See this example's README.
"""

import asyncio
import sys

from ask_agentdeck.agent import ask
from ask_agentdeck.corpus import DocsCorpus

from agentdeck import Deck


async def main(question: str) -> None:
    corpus = DocsCorpus()
    # The *type* here, the instance below. Declaring it is what makes build() check every
    # Context[...] in the catalog — both tools — before a single question is asked.
    async with Deck(agents=[ask], context=DocsCorpus) as deck:
        result = await deck.run("AskAgentDeck", question, context=corpus)
        print(result.output)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "how do I create an agent?"))
