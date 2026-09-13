from agentdeck import WorkflowCtx, workflow


@workflow
async def shift_handover(ctx: WorkflowCtx, area: str) -> str:
    """Write a handover note for one area, asking the outgoing operator what to flag."""
    severity = await ctx.ask(
        f"how did {area} run this shift?",
        options=["quiet", "busy", "problems"],
    )
    if severity == "problems":
        detail = await ctx.ask("what should the next shift look at first?")
        return f"{area}: problems. First: {detail}"
    return f"{area}: {severity}, nothing to escalate."
