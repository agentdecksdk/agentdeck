"""``Binding``, ``BindingInfo`` and the endpoint types: shape only, no concrete binding exists
yet (#548/#552 add the first)."""

import dataclasses
from typing import TYPE_CHECKING

import pytest

from agentdeck.bindings import PROTOCOL_SPI_VERSION, BindingInfo, HttpEndpoint, StdioEndpoint

if TYPE_CHECKING:
    from agentdeck.bindings.binding import Binding


def test_protocol_spi_version_is_1():
    assert PROTOCOL_SPI_VERSION == 1


def test_binding_info_and_endpoints_are_frozen():
    info = BindingInfo(name="a2a", kind="protocol", transport="http", spi_version=1, advertises=frozenset({"hitl"}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.name = "other"  # type: ignore[misc]

    endpoint = HttpEndpoint(path="/a2a", app=object())
    with pytest.raises(dataclasses.FrozenInstanceError):
        endpoint.path = "/other"  # type: ignore[misc]


def test_a_minimal_class_satisfies_binding_structurally():
    """No isinstance check (``Binding`` is not ``@runtime_checkable``, deliberately  -  it is a
    typing contract, not a base class with behavior); this proves the shape is satisfiable at
    all, which the fixture plugin under a future issue exercises for real."""

    class _Fixture:
        def __init__(self) -> None:
            self.info = BindingInfo(
                name="fixture",
                kind="channel",
                transport="stdio",
                spi_version=PROTOCOL_SPI_VERSION,
                advertises=frozenset(),
            )

        def build(self, gateway: object) -> StdioEndpoint:
            async def run() -> None:
                return None

            return StdioEndpoint(run=run)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    binding: Binding = _Fixture()
    endpoint = binding.build(gateway=object())
    assert isinstance(endpoint, StdioEndpoint)
