"""``DeckGateway``: the stable interface from a protocol into a Deck
(``docs/design/protocols/gateway.md``).

``Deck``/``Run`` are imported for typing only; this module reaches a Deck through its public
surface alone (``.runs``, ``.agents``, ``.workflows``, ``.settings``).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal

from pydantic import create_model

from agentdeck.errors import (
    DuplicateKeyError,
    InputError,
    NotFoundError,
    RunStateError,
    SessionBusyError,
    UnsupportedControlError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.authoring.native import NativeDefinition
    from agentdeck.core.status import RunStatus
    from agentdeck.deck import Deck, Run

JsonSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TargetInfo:
    """One agent or workflow a protocol may start a run against."""

    name: str
    kind: Literal["agent", "workflow"]
    description: str | None
    input_schema: JsonSchema | None


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What varies by deployment, never by run. ``gateway.md`` holds what each flag does and
    does not mean."""

    control: bool
    durable: bool


class GatewayFailureCode(Enum):
    NOT_FOUND = auto()
    INVALID_INPUT = auto()
    CONFLICT = auto()
    BUSY = auto()
    UNSUPPORTED = auto()
    INTERNAL = auto()


_INTERNAL_MESSAGE = "internal error"


class GatewayError(Exception):
    """The one exception a binding catches.

    ``message`` is wire-safe only for ``NOT_FOUND``, ``BUSY``, ``CONFLICT`` and ``INVALID_INPUT``.
    An ``INTERNAL`` message is replaced here, not merely documented: a caller cannot leak
    configuration values or a skill's stderr through one, whatever it passes. ``cause`` is for
    logging, not display.
    """

    def __init__(self, code: GatewayFailureCode, message: str, cause: BaseException | None = None) -> None:
        if code is GatewayFailureCode.INTERNAL:
            message = _INTERNAL_MESSAGE
        super().__init__(message)
        self.code = code
        self.message = message
        self.cause = cause


def _map_failure(exc: Exception) -> GatewayError:
    """``agentdeck/serve.py``'s exception handlers, as one function instead of one route each."""
    if isinstance(exc, NotFoundError):
        return GatewayError(GatewayFailureCode.NOT_FOUND, str(exc), exc)
    if isinstance(exc, SessionBusyError):
        return GatewayError(GatewayFailureCode.BUSY, str(exc), exc)
    if isinstance(exc, (RunStateError, DuplicateKeyError)):
        return GatewayError(GatewayFailureCode.CONFLICT, str(exc), exc)
    if isinstance(exc, InputError):
        return GatewayError(GatewayFailureCode.INVALID_INPUT, str(exc), exc)
    if isinstance(exc, UnsupportedControlError):
        return GatewayError(GatewayFailureCode.UNSUPPORTED, str(exc), exc)
    return GatewayError(GatewayFailureCode.INTERNAL, _INTERNAL_MESSAGE, exc)


def _workflow_schema(definition: NativeDefinition) -> JsonSchema | None:
    """The object schema for a workflow's input, or ``None`` if it takes none."""
    parameters = definition.analysis.visible_parameters
    if not parameters:
        return None
    fields: dict[str, Any] = {
        parameter.name: (Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation, ...)
        for parameter in parameters
    }
    model = create_model(f"{definition.name}_input", **fields)
    return model.model_json_schema()


class DeckGateway:
    """A facade over :attr:`Deck.runs` adding what a protocol needs: ``targets()``,
    ``capabilities`` and failure classification."""

    def __init__(self, deck: Deck) -> None:
        self._deck = deck

    def targets(self) -> Sequence[TargetInfo]:
        """Every agent and workflow in the deck's catalog.

        A workflow's schema marks every field required regardless of its own default, because
        ``NativeExecutor._arguments`` takes no partial mapping for a multi-parameter workflow.
        """
        agents = [
            TargetInfo(name=agent.name, kind="agent", description=agent.handoff_description, input_schema=None)
            for agent in self._deck.agents.values()
        ]
        workflows = [
            TargetInfo(
                name=workflow.name,
                kind="workflow",
                description=workflow.description or None,
                input_schema=_workflow_schema(workflow),
            )
            for workflow in self._deck.workflows.values()
        ]
        return [*agents, *workflows]

    @property
    def capabilities(self) -> Capabilities:
        """See :class:`Capabilities`: ``memory://`` reports ``False``, anything else ``True``."""
        settings = self._deck.settings
        return Capabilities(
            control=_is_configured(settings.control.url),
            durable=_is_configured(settings.events.url),
        )

    async def start(
        self,
        target: str,
        input: Any,
        *,
        session_id: str | None = None,
        namespace: str | None = None,
        key: str | None = None,
        context: object = None,
    ) -> Run:
        """:meth:`Runs.start`, with every failure mapped to a :class:`GatewayError`."""
        try:
            return await self._deck.runs.start(
                target, input, session_id=session_id, namespace=namespace, key=key, context=context
            )
        except Exception as exc:
            raise _map_failure(exc) from exc

    async def get_run(self, run_id: str, *, namespace: str | None = None) -> Run:
        """:meth:`Runs.get`, with every failure mapped to a :class:`GatewayError`."""
        try:
            return await self._deck.runs.get(run_id, namespace=namespace)
        except Exception as exc:
            raise _map_failure(exc) from exc

    async def list_runs(
        self, *, namespace: str | None = None, status: RunStatus | None = None, limit: int | None = None
    ) -> Sequence[Run]:
        """:meth:`Runs.list`, with every failure mapped to a :class:`GatewayError`."""
        try:
            return await self._deck.runs.list(namespace=namespace, status=status, limit=limit)
        except Exception as exc:
            raise _map_failure(exc) from exc


def _is_configured(url: str) -> bool:
    """``memory://`` is every backend's unconfigured default; anything else was set on purpose."""
    return url.partition("://")[0] != "memory"


__all__ = [
    "Capabilities",
    "DeckGateway",
    "GatewayError",
    "GatewayFailureCode",
    "TargetInfo",
]
