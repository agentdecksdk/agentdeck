import json
from pathlib import Path

from agents import function_tool

from agentdeck import Agent

_EXAMPLE_DIR = Path(__file__).resolve().parents[3]
_NOTES_FILE = _EXAMPLE_DIR / "handover_notes.json"

_SHIFTS = {
    "2026-03-02": "Marcus, on until 22:00",
    "2026-03-03": "Priya, on until 22:00",
    "2026-03-04": "nobody scheduled yet",
}


@function_tool
def lookup_shift(date: str) -> str:
    """Look up who is on shift for one date (YYYY-MM-DD)."""
    return _SHIFTS.get(date, "no shift recorded for that date")


@function_tool
def file_handover_note(date: str, text: str) -> str:
    """Record a handover note against one date (YYYY-MM-DD)."""
    notes = json.loads(_NOTES_FILE.read_text()) if _NOTES_FILE.exists() else {}
    notes.setdefault(date, []).append(text)
    _NOTES_FILE.write_text(json.dumps(notes, indent=2))
    return f"note filed for {date}"


handover_desk = Agent(
    name="HandoverDesk",
    instructions=(
        "You are the shift handover desk. Call lookup_shift before answering who is on a date, and "
        "never guess. When asked to leave a note for the next shift, load the shift-notes skill "
        "first, then call file_handover_note once with the date the note applies to."
    ),
    tools=[lookup_shift, file_handover_note],
    skills=["shift-notes"],
)
