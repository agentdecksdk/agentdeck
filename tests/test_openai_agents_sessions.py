"""``SessionFactory`` (#253/#274): the redis client and ``agents.extensions.memory.RedisSession``
are resolved lazily, inside the branch that actually needs them, so a base install — no
``[redis]`` extra — can still import this module and run the default (non-redis) session path.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

from agentdeck.adapters.engines.openai_agents.sessions import ExecutionStore, SessionFactory
from agentdeck.runtime.settings import SessionSettings


def test_from_settings_returns_none_when_no_url():
    assert SessionFactory.from_settings(SessionSettings(url=None)) is None


def test_from_settings_builds_a_redis_backed_factory_from_a_url():
    """No server needed: `Redis.from_url` connects lazily, so wiring is checkable without one —
    the same pattern `test_composition.py`'s redis event-store tests already use."""
    factory = SessionFactory.from_settings(
        SessionSettings(url="redis://localhost:6379/0", redis_key_prefix="p", redis_ttl=60)
    )

    assert factory is not None
    assert factory._key_prefix == "p"  # noqa: SLF001 — asserting the constructor wiring, not behavior
    assert factory._ttl == 60  # noqa: SLF001


def test_session_for_returns_a_redis_session():
    from agents.extensions.memory import RedisSession

    factory = SessionFactory.from_settings(SessionSettings(url="redis://localhost:6379/0"))

    session = factory.session_for("some-key")

    assert isinstance(session, RedisSession)


def test_execution_store_falls_back_to_sqlite_with_no_session_factory():
    """The default path (`AGENTDECK_SESSION` unset) never touches redis at all."""
    from agents import SQLiteSession

    store = ExecutionStore(session_factory=None)

    from agentdeck.core.context import RunContext

    ctx = RunContext(run_id="r-1", session_id="s-1", namespace="ns")
    session = store.session_for(ctx)

    assert isinstance(session, SQLiteSession)


def test_importing_the_openai_agents_adapter_never_imports_redis():
    """#274's actual regression: `sessions.py` used to import both `redis.asyncio` and
    `agents.extensions.memory` (whose `RedisSession` import pulls redis in behind an
    ``__getattr__`` guard) at module scope, and the adapter's own `__init__.py` imports
    `sessions.py` unconditionally — so a base install without the `[redis]` extra could not
    import the adapter at all, whatever `AGENTDECK_SESSION` said.

    A fresh subprocess with ``sys.modules['redis'] = None`` before any import, because this
    process has already imported redis (the tests above need it installed) and `sys.modules`
    cannot unsee that — see `test_langfuse_sink.py`'s identical rationale for its own probe.
    """
    probe = textwrap.dedent(
        """
        import sys
        sys.modules["redis"] = None
        import agentdeck.adapters.engines.openai_agents
        assert "agents.extensions.memory.redis_session" not in sys.modules
        print("imported without redis")
        """
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)

    assert done.returncode == 0, done.stderr
    assert "imported without redis" in done.stdout


def test_a_redis_session_without_the_extra_names_the_install_command():
    """Selecting `AGENTDECK_SESSION=redis://...` without the `[redis]` extra must fail with an
    agentdeck error naming the install command, not a raw `ModuleNotFoundError` — the same
    contract the durability extras already give a missing sqlite/postgres saver."""
    probe = textwrap.dedent(
        """
        import sys
        sys.modules["redis"] = None
        from agentdeck.adapters.engines.openai_agents.sessions import SessionFactory
        from agentdeck.runtime.settings import SessionSettings
        try:
            SessionFactory.from_settings(SessionSettings(url="redis://localhost:6379/0"))
        except ImportError as exc:
            assert "redis" in str(exc)
            assert 'pip install "agentdeck-sdk[redis]"' in str(exc), str(exc)
            print("raised the right error")
        else:
            raise AssertionError("expected an ImportError")
        """
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)

    assert done.returncode == 0, done.stderr
    assert "raised the right error" in done.stdout
