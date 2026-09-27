# Bank Agent source map

Resolve these paths from the Bank Agent installation root.

## User interface and documented behavior

- `README.md`: supported workflows, installation, sessions, workspaces,
  instructions, skills, providers, approvals, and stated scope.
- `HELP.txt`: current chat commands and keyboard controls.
- `Open Bank Agent.cmd`: desktop-launch behavior and exit-code handling.
- `scripts/start-bank-agent.ps1`: environment loading, provider selection,
  transcript logging, `uv` invocation, and argument forwarding.
- `scripts/install-bank-agent-command.ps1`: user-level `bank-agent` command and
  PATH installation.
- `scripts/setup-offline.ps1` and the import/export scripts: offline setup and
  wheel-transfer behavior.

## Core implementation

- `bank_agent/main.py`: argument parsing, runtime paths, activity display,
  conversation setup, approval decisions, instruction loading, workspace
  resolution, chat commands, and the main session loop.
- `bank_agent/sessions.py`: session record schema and index, lookup, lifecycle,
  archive/delete behavior, locks, launch-directory matching, and picker UIs.
- `bank_agent/config.py`: supported providers, environment-variable precedence,
  model normalization, endpoint construction, timeouts, retries, and validation.
- `bank_agent/allowlist.py`: persistent exact-command allowlist storage.
- `bank_agent/skills.py`: skill discovery, rejection rules, draft validation,
  duplicate detection, and installation.
- `bank_agent/search_tool.py`: compatibility search schema and its read
  boundaries.
- `bank_agent/gemini_connector.py`: translation between LiteLLM/OpenAI-shaped
  requests and the bank Gemini connector.
- `pyproject.toml`: runtime dependencies, command entry point, package data, and
  dependency constraints.

## Development and current status

- `tests/`: observable behavior and regression coverage. Use tests to confirm
  edge cases, not as the sole source for intended behavior.
- `docs/guided-skill-creation-status.md`: why guided skill creation is currently
  unavailable and what is required before it can be re-enabled.
- `agent-workspace/AGENTS.md`: local cross-workspace organizational guidance.
  It is runtime instruction data, not a universal product specification.
- Workspace-specific `AGENTS.md` files apply only to their selected workspace.
  Files with that name inside cloned repositories are not harness instructions.

## OpenHands boundary

The vendored `vendor/openhands-sdk/` and `vendor/openhands-tools/` trees explain
SDK behavior beneath the wrapper. Consult only the relevant implementation when
the question specifically depends on it—for example condensation internals or
tool execution semantics. First establish how `bank_agent/` configures or
restricts that behavior.

Never inspect `.env`, `sessions/`, `.openhands/`, `policy/allowlist.json`, startup
logs, or project contents for an ordinary harness explanation. Those locations
may contain secrets, private conversations, commands, or unrelated developer
code.
