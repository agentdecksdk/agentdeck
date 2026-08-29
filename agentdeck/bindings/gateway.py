"""``ProtocolGateway``: the stable interface from a protocol into a Deck.

Wraps :attr:`Deck.runs` rather than growing it or handing a plugin the Deck itself
(``docs/design/protocols/gateway.md``): ``targets()``, ``capabilities`` and one exception type
are what a plugin needs beyond ``start``/``get``/``list``, so it never has to understand
:mod:`agentdeck.errors`. ``Deck``/``Run`` are imported for typing only; this module reaches a
Deck through its public surface alone (``.runs``, ``.agents``, ``.workflows``, ``.settings``).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal

from pydantic import create_model

from agentdeck.errors import (
    DuplicateKeyError,
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
    """One agent or workflow a protocol may start a run against.

    ``input_schema`` is ``None`` for a free-text agent; a workflow's is built from its own
    parameters, so a target the catalog changes never drifts from what starting it actually takes.
    """

    name: str
    kind: Literal["agent", "workflow"]
    description: str | None
    input_schema: JsonSchema | None


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What varies by deployment, never by run  -  advertised once, before any run exists.

    ``control=False`` under the default ``memory://`` backend still delivers pause/cancel/resume
    to a run *executing in this process* (``deck.py``'s ``_NO_CONTROL_PORT``); it means signals
    cannot reach a run in another worker, never "control unsupported"  -  ``run.can`` and the
    ``Run`` methods stay the per-run authority. ``durable=False`` means the log does not survive
    a restart and is unreadable from another process; both flip ``True`` off ``memory://``.
    """

    control: bool
    durable: bool


class GatewayFailureCode(Enum):
    NOT_FOUND = auto()
    INVALID_INPUT = auto()
    CONFLICT = auto()
    BUSY = auto()
    UNSUPPORTED = auto()
    CANCELLED = auto()
    INTERNAL = auto()


_INTERNAL_MESSAGE = "internal error"


class GatewayError(Exception):
    """The one exception a binding needs to catch. A plugin must not understand AgentDeck's own
    exception classes, so every failure out of :class:`ProtocolGateway` arrives as this.

    ``message`` is wire-safe only for ``NOT_FOUND``, ``BUSY``, ``CONFLICT`` and ``INVALID_INPUT``;
    ``INTERNAL`` always carries the fixed ``"internal error"`` and never the cause's own text,
    which may hold configuration values or a skill's stderr. ``cause`` is for logging, not display.
    """

    def __init__(self, code: GatewayFailureCode, message: str, cause: BaseException | None = None) -> None:
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
    if isinstance(exc, (TypeError, ValueError)):
        return GatewayError(GatewayFailureCode.INVALID_INPUT, str(exc), exc)
    if isinstance(exc, UnsupportedControlError):
        return GatewayError(GatewayFailureCode.UNSUPPORTED, str(exc), exc)
    return GatewayError(GatewayFailureCode.INTERNAL, _INTERNAL_MESSAGE, exc)


def _workflow_schema(definition: NativeDefinition) -> JsonSchema | None:
    """The object schema a caller's mapping input must satisfy, built from the parameters the
    body itself declares (``NativeExecutor._arguments`` binds a multi-parameter workflow's input
    by name against exactly these). ``None`` for a workflow that takes no input at all; a
    single-parameter workflow also accepts its value bare, which this schema does not represent.
    """
    parameters = definition.analysis.visible_parameters
    if not parameters:
        return None
    fields: dict[str, Any] = {
        parameter.name: (
            Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation,
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
        for parameter in parameters
    }
    model = create_model(f"{definition.name}_input", **fields)
    return model.model_json_schema()


class ProtocolGateway:
    """The stable surface a :class:`~agentdeck.bindings.binding.Binding` builds an
    :class:`~agentdeck.bindings.binding.Endpoint` against. A thin facade over :attr:`Deck.runs`:
    it delegates every run operation and adds only what ``Runs`` lacks for a protocol  -
    ``targets()``, ``capabilities`` and failure classification.
    """

    def __init__(self, deck: Deck) -> None:
        self._deck = deck

    def targets(self) -> Sequence[TargetInfo]:
        """Every agent and workflow in the deck's catalog, as a protocol advertises them."""
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
        """See :class:`Capabilities`. Read from :attr:`Deck.settings` alone, so this needs
        nothing beyond the deck's public surface: ``memory://`` (either backend's default)
        reports ``False``, anything else ``True``."""
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
        """:meth:`Runs.start`, with every failure arriving as one :class:`GatewayError`."""
        try:
            return await self._deck.runs.start(
                target, input, session_id=session_id, namespace=namespace, key=key, context=context
            )
        except Exception as exc:
            raise _map_failure(exc) from exc

    async def get_run(self, run_id: str, *, namespace: str | None = None) -> Run:
        """:meth:`Runs.get`, with every failure arriving as one :class:`GatewayError`."""
        try:
            return await self._deck.runs.get(run_id, namespace=namespace)
        except Exception as exc:
            raise _map_failure(exc) from exc

    async def list_runs(
        self, *, namespace: str | None = None, status: RunStatus | None = None, limit: int | None = None
    ) -> Sequence[Run]:
        """:meth:`Runs.list`, with every failure arriving as one :class:`GatewayError`."""
        try:
            return await self._deck.runs.list(namespace=namespace, status=status, limit=limit)
        except Exception as exc:
            raise _map_failure(exc) from exc


def _is_configured(url: str) -> bool:
    """``memory://`` is every backend's own unconfigured default; anything else was set on
    purpose."""
    return url.partition("://")[0] != "memory"


__all__ = [
    "Capabilities",
    "GatewayError",
    "GatewayFailureCode",
    "JsonSchema",
    "ProtocolGateway",
    "TargetInfo",
]
