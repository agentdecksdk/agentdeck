"""``ProtocolGateway``: the stable interface from a protocol into a Deck
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
    INTERNAL = auto()


_INTERNAL_MESSAGE = "internal error"


class GatewayError(Exception):
    """What every :class:`ProtocolGateway` method raises on failure; a :class:`~agentdeck.deck.Run`
    method (``cancel``, ``pause``, ``resume``, ``answer``) is not one and keeps its own public
    error instead (``RunStateError``, ``UnsupportedControlError``, ``RunSuspendedError``), mapped by a binding.

    ``message`` is wire-safe only for ``NOT_FOUND``, ``BUSY``, ``CONFLICT`` and ``INVALID_INPUT``;
    ``INTERNAL`` always carries the fixed ``"internal error"``, never the cause's own text, which
    may hold configuration values or a skill's stderr. ``cause`` is for logging, not display.
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
    if isinstance(exc, InputError):
        return GatewayError(GatewayFailureCode.INVALID_INPUT, str(exc), exc)
    if isinstance(exc, UnsupportedControlError):
        return GatewayError(GatewayFailureCode.UNSUPPORTED, str(exc), exc)
    return GatewayError(GatewayFailureCode.INTERNAL, _INTERNAL_MESSAGE, exc)


def _workflow_schema(definition: NativeDefinition) -> JsonSchema | None:
    """The object schema for a workflow's input, or ``None`` if it takes none. Every field is
    marked required: :meth:`ProtocolGateway.targets` explains why.
    """
    parameters = definition.analysis.visible_parameters
    if not parameters:
        return None
    fields: dict[str, Any] = {
        parameter.name: (Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation, ...)
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
        """Every agent and workflow in the deck's catalog, as a protocol advertises them.

        ``description`` is the agent's ``handoff_description`` or the workflow's own ``description``;
        ``kind`` distinguishes them. ``input_schema`` is ``None`` for a free-text agent; for a workflow
        it is the schema built from its parameters, every field required regardless of its own default,
        because ``NativeExecutor._arguments`` takes no partial mapping for a multi-parameter workflow.
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
        """Begin a run against ``target`` with the same parameters as :meth:`Runs.start`, mapping every
        failure to one :class:`GatewayError`.

        ``NOT_FOUND``: no target named ``target``. ``INVALID_INPUT``: ``input`` the target cannot take.
        ``BUSY``: ``session_id`` already holds a run in flight, named in the message. ``CONFLICT``:
        ``(namespace, key)`` already held. ``INTERNAL``: anything else, with a fixed message.
        """
        try:
            return await self._deck.runs.start(
                target, input, session_id=session_id, namespace=namespace, key=key, context=context
            )
        except Exception as exc:
            raise _map_failure(exc) from exc

    async def get_run(self, run_id: str, *, namespace: str | None = None) -> Run:
        """Rehydrate the run named ``run_id``, scoped to ``namespace`` exactly like
        :meth:`Runs.get`: ``run_id`` in a namespace other than the one it was started in is
        ``NOT_FOUND``, never a cross-namespace lookup. ``INTERNAL`` for anything else. Both
        arrive as a :class:`GatewayError`.
        """
        try:
            return await self._deck.runs.get(run_id, namespace=namespace)
        except Exception as exc:
            raise _map_failure(exc) from exc

    async def list_runs(
        self, *, namespace: str | None = None, status: RunStatus | None = None, limit: int | None = None
    ) -> Sequence[Run]:
        """Every run in ``namespace``, per :meth:`Runs.list`: stays scoped to that one
        namespace, never spanning several, and an unknown ``namespace`` is an empty sequence,
        never ``NOT_FOUND``  -  a namespace is a partition, not an identity the store can fail
        to find. Order is not guaranteed. Any failure arrives as a :class:`GatewayError` with
        code ``INTERNAL``.
        """
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
