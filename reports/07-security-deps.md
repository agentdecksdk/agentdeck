# Security and Dependency Risk

AgentDeck is a self-hosted harness, so the audit question is whether it tells the truth about its trust boundaries and avoids the own-goals. On the code side it is unusually clean: no dynamic execution primitives anywhere, fully parameterized SQL, deliberate key escaping, and error paths that refuse to ship exception text to a client. On the surface side the story is weaker: the HTTP layer has no caller identity, and SECURITY.md's in-scope list promises an isolation boundary that no mechanism in the codebase provides.

## Findings

### No dynamic-execution primitives anywhere in the package [GOOD] (severity: high)
A repo-wide grep for `pickle`, `marshal`, `eval(`, `exec(`, `subprocess`, `os.system`, `shell=True`, `yaml.load` returns exactly two hits, both the word "subprocess" inside a docstring. The one YAML parse in the package is `safe_load`.
```python
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
```
Evidence: `agentdeck/skills/bundle.py:57`

### Every SQL statement is parameterized; identifiers are composed, not interpolated [GOOD] (severity: high)
The Postgres store builds its schema-qualified table name with `psycopg.sql.Identifier` (quoted by the driver) and binds every value with `%s`. No f-string in any store, control port, or lease port interpolates a non-constant into SQL.
```python
        table = sql.Identifier(schema, "events")
        # Composed once: the schema name is an identifier, so it is quoted by psycopg
        # rather than interpolated  -  a schema is caller-supplied configuration.
        self._insert = sql.SQL(
            "INSERT INTO {table} (namespace, log_key, run_id, key, seq, data) VALUES (%s, %s, %s, %s, %s, %s::jsonb)"
        ).format(table=table)
```
Evidence: `agentdeck/adapters/stores/postgres/store.py:100`
Ref: https://www.psycopg.org/psycopg3/docs/api/sql.html

### The only f-string SQL is a generated placeholder list [GOOD] (severity: medium)
The one dynamic query shape in the codebase builds `IN (?,?,?)` from a length, never from values, and binds the ids. The other f-string SQL sites interpolate module constants (`_BUSY_TIMEOUT_MS`, `_EXPIRY`, `_NOW`) only.
```python
        placeholders = ",".join("?" * len(run_ids))
        rows = self._conn.execute(
            f"SELECT run_id FROM leases WHERE run_id IN ({placeholders}) AND expires_at <= {_NOW}", run_ids
        )
```
Evidence: `agentdeck/adapters/leases/sqlite/port.py:121`

### Redis key segments are percent-escaped so a caller-supplied id cannot forge a namespace [GOOD] (severity: medium)
`session_id` arrives from an HTTP body and becomes part of a colon-delimited Redis key. Escaping it closes the store-level equivalent of an injection, and the docstring names the exact confusion it prevents.
```python
def _segment(value: str) -> str:
    """One key segment, escaped so ``:`` inside a namespace or session id cannot forge another.

    Without this, namespace ``"a:b"`` + log ``"c"`` and namespace ``"a"`` + log ``"b:c"`` are the
    same key, and two namespaces read each other's runs  -  the isolation every store owes.
    """
    return quote(value, safe="")
```
Evidence: `agentdeck/adapters/stores/redis/store.py:72`

### Exception text never reaches a client, streaming or not [GOOD] (severity: high)
Non-streaming responses funnel every non-client-caused error into a generic 500 with the real message logged. The streaming path, which cannot use those handlers once headers are on the wire, independently ships the exception's type name and nothing else.
```python
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"
```
Evidence: `agentdeck/surfaces/serve/compat.py:97` and `agentdeck/serve.py:152-157`

### The MCP transport allowlist refuses stdio, so a Claude-Code spec cannot spawn a process [GOOD] (severity: medium)
`.mcp.json` accepts Claude Code's shape with `extra="allow"`, which means a `command` / `args` entry parses. It cannot execute: transport is checked against a closed set, and a `stdio` entry is rejected as a config error rather than launched.
```python
    transport = (spec.get("type") or "http").lower()
    if transport not in {"http", "streamable-http", "streamable_http"}:
        raise ConfigError(f"MCP server '{name}': unsupported transport '{transport}'")
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:36`

### Custom-CA support instead of a `verify=False` escape hatch [GOOD] (severity: medium)
The self-hosted-endpoint case is the usual reason a project grows an insecure-TLS flag. AgentDeck solved it with a CA bundle path passed to `httpx`, and `verify` is never set to `False` anywhere in the package.
```python
                http_client=httpx.AsyncClient(verify=settings.ca_bundle),
```
Evidence: `agentdeck/adapters/engines/openai_agents/runconfig.py:135`

### Tracing is opt-in and defaults to localhost [GOOD] (severity: medium)
Traces carry full prompts, tool arguments, and tool results. Nothing is exported unless both Langfuse keys are set, and the default endpoint is a local one, so a bare checkout sends no run content off the box.
```python
    base_url: str = Field(default="http://localhost:3000", description="Langfuse endpoint.")
    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)
```
Evidence: `agentdeck/runtime/settings.py:402,416-417`

### SECURITY.md is honest about the two things that are not defended [GOOD] (severity: medium)
Most projects bury "we execute whatever the model picks" or claim a sandbox they do not have. This states both as design, names the consequence, and gives the mitigation.
```text
- **A model-chosen tool call is trusted by design.** ... There is no allowlist, no
  confirmation step, and no privilege boundary between the model's decision and your
  function  -  so a prompt injection that reaches an agent reaches every tool that agent
  holds. ... keep the destructive ones behind a workflow with a human approval.
```
Evidence: `SECURITY.md:40-46`

### The Postgres DSN is deliberately kept out of error messages [GOOD] (severity: low)
The checkpointer distinguishes a filesystem path (safe to name) from a DSN (not), and says so. This is the correct rule, which is what makes the one place it is broken (below) a defect rather than an omission.
```python
    except psycopg.Error as exc:
        # No DSN in the message, unlike the sqlite branch: a DSN can carry a password, and
        # unlike a filesystem path, that is a secret.
        raise StoreError(f"cannot open the workflow checkpoint (AGENTDECK_CHECKPOINT): {exc}") from exc
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:199-203`

### Config and `.env` resolve from cwd only, with no upward directory walk [GOOD] (severity: low)
`python-dotenv`'s own `find_dotenv` walks up the tree, which lets a parent directory quietly supply credentials. Both resolvers here stop at `Path.cwd()`.
```python
    local = Path.cwd() / "config.yaml"
    return local if local.is_file() else PACKAGED_DEFAULT_YAML
```
Evidence: `agentdeck/runtime/settings.py:70-71`

### Dependabot covers both `uv` and the pinned action refs, with `uv.lock` committed [GOOD] (severity: medium)
Two ecosystems, weekly, resolved through the committed lockfile. That is the whole of what a one-maintainer project realistically needs to not fall behind on transitive CVEs.
```yaml
  - package-ecosystem: "uv"
    directory: "/"
  - package-ecosystem: "github-actions"
    directory: "/"
```
Evidence: `.github/dependabot.yml:6-14`

### SECURITY.md claims a caller-isolation boundary that no mechanism implements [BAD] (severity: high)
SECURITY.md puts "a request reaching a run, a session, or an event log that belongs to another caller" in scope. There is no caller identity anywhere in the surface: `session_id` comes from the request body, becomes the log key, and the runtime reads that log's full history into the next turn. Any unauthenticated caller resumes any conversation by naming it. README's "no auth" non-goal is honest; this in-scope claim is not compatible with it.
```python
    def log_key(self) -> str:
        return self.session_id or self.run_id
```
Evidence: `agentdeck/core/context.py:102-105`, consumed at `agentdeck/runtime/service.py:186`, promised at `SECURITY.md:30-31`

### The approval door is unauthenticated, and a fronting gateway cannot fix it [BAD] (severity: high)
SECURITY.md's stated mitigation for destructive tools is "keep them behind a workflow with a human approval". The endpoint that answers that approval takes a `thread_id` and a value, with no notion of who is entitled to answer. Blanket gateway auth does not help: it cannot distinguish the intended approver from any other authenticated caller, which is precisely the distinction an approval exists to make.
```python
    @api.post("/workflows/{name}/{thread_id}/resume")
    async def resume_workflow(name: str, thread_id: str, body: dict[str, Any]) -> Any:
        if "value" not in body:
            raise HTTPException(status_code=422, detail="missing field: value")
        return await deck._answer(paused.run_id, body["value"])
```
Evidence: `agentdeck/serve.py:282-296`

### The console script and compose file default to binding every interface [BAD] (severity: high)
This is the concrete, one-line-fixable version of the no-auth posture. An unauthenticated surface that runs models on someone else's budget should require an explicit act to leave the loopback; today the default is the opposite, and `docker-compose up` publishes it to the host with no override needed.
```python
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="interface to bind (env: HOST)")
```
Evidence: `agentdeck/serve.py:350`, `docker-compose.yml:4-5`

### The event schema has no actor field, so control actions are unattributable [BAD] (severity: medium)
`RunPaused`, `RunResumed`, and `RunCancelled` carry a free-text `reason` supplied by the requester and nothing else. For a system whose central claim is one canonical event log per turn, the log cannot answer "who cancelled this run" or "who approved this spend", even for an operator who has bolted identity on at a gateway.
```python
class RunCancelled(CoreModel):
    reason: str | None = None
```
Evidence: `agentdeck/core/events.py:160-164`

### MCP tool discovery is unconstrained, and the SDK's own filter is unused [BAD] (severity: medium)
SECURITY.md's out-of-scope clause covers "every tool **you declare**". MCP tools are the one category the user does not declare: the server returns the tool list at connect time, its descriptions go into the agent's instructions, and its results go into the model's context. A compromised or swapped MCP server therefore both poisons the prompt and gains a tool the operator never wrote. `openai-agents` ships `tool_filter` / `create_static_tool_filter` for exactly this; a grep of `agentdeck/` and `tests/` finds zero uses.
```python
    params: dict[str, Any] = {"url": url}
    if isinstance(headers := spec.get("headers"), dict):
        params["headers"] = headers
    return MCPServerStreamableHttpResilient(params=params, name=name)
```
Evidence: `agentdeck/adapters/tools/mcp/lifecycle.py:42-47`
Ref: https://openai.github.io/openai-agents-python/mcp/

### The unknown-scheme error echoes the whole `AGENTDECK_EVENTS` URL, password included [BAD] (severity: medium)
SECURITY.md lists "an API key in a log line, an event payload, or an error message" as in scope, and the checkpointer follows that rule deliberately. This branch does not: a typo'd scheme on a `postgresql://user:password@host/db` or `rediss://:password@host` value puts the credential in the exception message and the log line that records it.
```python
    raise ValueError(
        f"unknown event store scheme {scheme!r} in AGENTDECK_EVENTS={events.url!r}; expected memory, sqlite, "
        f"redis, rediss, or postgresql  -  see {_STORE_DOCS}"
    )
```
Evidence: `agentdeck/composition.py:256-259`

### Run-control routes accept any run id with no ownership check and no rate limit [BAD] (severity: medium)
`pause` / `cancel` / `resume` take a bare `run_id`, and the wire deliberately answers `200 recorded: true` for ids that do not exist. The non-enumeration property is a real upside, but the flip side is that a leaked or guessed run id is sufficient to kill a stranger's in-flight run, with nothing recorded about who asked.
```python
    @api.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, body: dict[str, Any] | None = None) -> Any:
        return await _control(_deck()._cancel, run_id, body, "cancel")
```
Evidence: `agentdeck/serve.py:237-239`

### No request-size, rate, or concurrency limit on the chat endpoint [BAD] (severity: medium)
`POST /agents/{name}/chat` accepts an arbitrary-length `message` and forwards it to a paid model endpoint. There is no middleware on the app at all, so the only backpressure is the one-turn-per-session rule, which a caller defeats by varying `session_id`. The blast radius is the operator's provider bill, and the fix belongs in the app rather than being assumed of the gateway.
```python
    api = FastAPI(title="agentdeck", lifespan=lifespan)
    api.state.deck = None  # set by the lifespan; None means "not started yet"
```
Evidence: `agentdeck/serve.py:122-123`

### `.gitignore` covers `.env` but not the two other documented secret locations [BAD] (severity: medium)
`.mcp.json` holds `headers`, which is where an MCP bearer token goes, and `config.yaml` accepts `openai: api_key:` and `tavily: api_key:`. Both live at the project root and neither is ignored, while `.env` is. The design invites committing a credential.
```python
    headers: dict[str, str] = Field(default_factory=dict)
```
Evidence: `agentdeck/mcp.py:33`, `agentdeck/runtime/settings.py:221`, `.gitignore:9`

### Dockerfile runs as root, floats its base tag, and ignores the lockfile [BAD] (severity: medium)
Three separate defaults, one fix each: no `USER`, so the container process is root; `python:3.13-slim` is mutable, so two builds of the same commit differ; and `pip install ".[serve]"` re-resolves every range at build time, so the committed `uv.lock` governs development and governs nothing in the shipped image.
```dockerfile
FROM python:3.13-slim
COPY agentdeck/ agentdeck/
RUN pip install --no-cache-dir ".[serve]"
CMD ["agentdeck-serve"]
```
Evidence: `Dockerfile:1-9`

### SQLite backing files are created at the process umask [BAD] (severity: low)
The event log holds full conversation content, and the control and lease files are the CLI's only authorization boundary (whoever can write `control.db` can cancel any run). No `chmod` or `umask` call exists in the package, so on a default umask these land world-readable, and on a shared host that is both a content leak and a control bypass.
```python
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
```
Evidence: `agentdeck/adapters/stores/sqlite/store.py:75`, `agentdeck/adapters/control/sqlite/port.py:80`

### `aiosqlite` is imported and used directly but never declared [BAD] (severity: low)
It arrives only as a transitive dependency of `langgraph-checkpoint-sqlite`, and the code does not merely re-export it: it calls `aiosqlite.connect` and reaches into `conn._thread`. A minor upstream release that drops or renames the dep breaks the default durable-workflow path with an `ImportError` at a lazy import site.
```python
    import aiosqlite
```
Evidence: `agentdeck/adapters/engines/langgraph/checkpointer.py:152`
Ref: https://packaging.python.org/en/latest/specifications/pyproject-toml/

### `opentelemetry-sdk` is declared but never imported [BAD] (severity: low)
The `[observability]` extra installs it, and the only occurrence of the name in the package is a logger-name string literal. It is a package on disk that no code reaches, which is attack surface and CVE noise bought for nothing.
```python
_OTLP_EXPORTER_LOGGER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
```
Evidence: `agentdeck/adapters/telemetry/langfuse/client.py:36`, declared at `pyproject.toml`

### No vulnerability scanner in the gate or in CI [BAD] (severity: low)
`make check` is ruff, ty, import-linter, and pytest. Dependabot raises version bumps but nothing fails a build on a known-vulnerable resolved dependency, and with a committed `uv.lock` a `pip-audit` or `uv-secure` step is close to free.
```makefile
check: lint typecheck lint-imports test   ## full gate
```
Evidence: `Makefile:48`

### A `config.yaml` in the working directory can redirect the endpoint and supply the key [BAD] (severity: low)
`openai.base_url` and `openai.api_key` are both YAML-settable, and the resolver picks up `./config.yaml` with no marker or trust check. Running the CLI from an untrusted checkout points every model call at an attacker's endpoint. SECURITY.md's out-of-scope clause names `.agentdeck/`, not this file.
```python
    api_key: str = Field(
        default="",
```
Evidence: `agentdeck/runtime/settings.py:221`, resolved at `agentdeck/runtime/settings.py:70`

### The v2 skeleton surface lists every pending approval payload across every session [BAD] (severity: low)
`GET /v2/pending` returns each waiting run's `session_id`, `thread_id`, and full interrupt payload with no filter, and `POST /v2/resume` answers any of them. Nothing in the package wires either app today, so this is latent rather than live, but `build_app` is exported from the package and the module's own comment defers hardening to a review that has not happened.
```python
    @api.get("/v2/pending")
    async def pending() -> list[dict[str, Any]]:
        listing = await runtime.pending()
        return [
            {
                "run_id": p.run_id,
                "session_id": p.session_id,
                "thread_id": p.thread_id,
                "payload": p.payload,
            }
```
Evidence: `agentdeck/surfaces/serve/workflows.py:38-50`

## Bottom line

The code-level security discipline is better than most projects of this size: nothing is dynamically executed, every query is parameterized, key escaping and error redaction were thought about deliberately rather than added after a report, and SECURITY.md tells the truth about tool trust and the absent sandbox. The weakness is entirely at the HTTP boundary, where SECURITY.md's in-scope list promises per-caller isolation that no mechanism provides, the approval endpoint that its own mitigation advice depends on is open to anyone who can reach the port, and the shipped defaults bind every interface. Fix the defaults, add an actor to the control events, and either implement the isolation SECURITY.md claims or delete the claim.
