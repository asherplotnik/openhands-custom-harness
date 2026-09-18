# Bank Agent Improvement Task List

## Organization-independent

- Split the oversized `main.py` into focused modules: UI, conversation lifecycle, tools, approvals, and configuration.
- Enforce critical rules in code, not only through `AGENTS.md`, especially workspace boundaries and never modifying `main` or `master` directly.
- Improve provider compatibility and error handling, particularly malformed tool calls and connector-specific JSON differences.
- Add startup diagnostics that clearly identify missing configuration, credentials, certificates, CLI tools, or incompatible versions.
- Replace exact-command auto-approval with structured policies such as allowing defined categories of read-only Git commands.
- Add recovery from corrupted session indexes, incomplete writes, and interrupted operations.
- Build a regression and evaluation suite with realistic coding tasks, not only unit tests.
- Add provider and model selection, including fallback behavior, through Settings.
- Improve visibility into what the agent is doing without flooding the chat: current tool, elapsed time, and retry status.
- Package installation and upgrades more cleanly through Artifactory.

## Organization-dependent

- Add read-only CLI integrations for Splunk, Logs Explorer, `oc`, `kubectl`, GKE, JFrog/Xray, Bitbucket, and Jira.
- Build a central service map connecting `service -> repository -> image -> deployment -> namespace -> dependencies -> owner`.
- Resolve a deployed image tag back to the exact repository, branch, and commit.
- Add end-to-end request tracing using correlation IDs, trace IDs, pod logs, and service dependencies.
- Add environment profiles for development, testing, staging, and production.
- Define organization-specific command policies: unrestricted read-only inspection, approval for mutations, and stronger production restrictions.
- Maintain an organizational knowledge skill containing platform conventions, common failures, ownership, and operational procedures.
- Automatically discover repositories and deployments instead of depending entirely on manually maintained mappings.
- Redact secrets and sensitive log content before data reaches the model or session logs.
- Add a JFrog/Xray vulnerability workflow: CVE -> affected service -> dependency parent -> fix branch -> validation -> pull request.
- Build organization-specific evaluation scenarios that prove the harness handles real infrastructure correctly.

## Recommended implementation order

1. Modularize and strengthen the harness.
2. Add safe read-only infrastructure commands.
3. Build the service and infrastructure map.
4. Add end-to-end request tracing.
5. Add image-to-source resolution.
6. Only afterward allow automated operational changes.
