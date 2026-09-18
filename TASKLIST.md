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

### Global transaction journey tracing

Build a read-only tracing capability that accepts a log line or global transaction
ID and follows the request chronologically through Splunk and GKE Logs Explorer.

Known high-level journeys:

- Journey A — OCP: `client -> WebSEAL/F5 -> Titan`, followed by either:
  - `microservices -> API Connect -> ACE -> Mainframe`
  - `Composer monolith -> Mainframe/database`
- Journey B: `client -> F5 -> Apigee -> BFF -> microservices`, followed by
  either `ODS` or `API Connect -> Mainframe`.

Implementation shape:

- Create an organization journey skill containing the stable architecture,
  routing clues, naming conventions, known branches, common failures, and rules
  for interpreting results.
- Keep changing environment values in configuration rather than the skill.
- Define `dev`, `uat`, and `prod` profiles containing their Splunk indexes, GCP
  projects, clusters, namespaces, and service aliases. Do not store credentials
  or secrets in the repository.
- Keep production access permanently read-only and apply stricter time-range and
  result limits there.
- Add bounded, read-only `splunk-search` and `gke-log-search` tools. Add
  `oc-inspect`, `resolve-image`, and `find-source` later.
- Normalize results into timestamp, environment, layer, service, status, pod,
  image, transaction ID, and source-system fields before returning them to the
  model.
- Use the global transaction ID as the primary link between hops. Starting from
  any matching log, identify the journey and search only the expected next and
  previous layers around the same time window.
- Group temporary pods by stable workload/service labels. Treat an unknown
  service found through transaction-ID logs as discovered runtime topology and
  report it separately instead of inventing a permanent relationship.
- Return a chronological journey, the likely failure point, missing expected
  hops, retries, relevant services/pods/images, and confidence level.

Information to collect when bank-environment access is available:

- [ ] Exact command or API currently available for querying Splunk.
- [ ] Whether GKE logs are queried through `gcloud logging read`, `kubectl logs`,
      an internal CLI, or a combination.
- [ ] Splunk indexes and GCP project/cluster/namespace mappings for `dev`, `uat`,
      and `prod`.
- [ ] Sanitized example log lines from every major layer and the exact global
      transaction ID field/header names used by each one.
- [ ] Log patterns that distinguish Journey A, Journey B, and their branches.
- [ ] Stable Kubernetes/OpenShift labels that identify service, deployment,
      owner, environment, image, and version.
- [ ] Authentication method and read-only authorization boundaries for every
      CLI, without recording credentials.

## Recommended implementation order

1. Modularize and strengthen the harness.
2. Add safe read-only infrastructure commands.
3. Build the service and infrastructure map.
4. Add end-to-end request tracing.
5. Add image-to-source resolution.
6. Only afterward allow automated operational changes.
