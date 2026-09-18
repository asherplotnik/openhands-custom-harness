# Bank Agent Prototype

This is a deliberately small local coding-agent prototype. It uses the
remediated OpenHands SDK as a dependency and does not modify it.

Its `uv` configuration repeats the remediation repository's current
dependency constraints. The sole deliberate prototype exception is a
Windows-compatible `litellm==1.100.0`: the repository's 1.93.0 pin is a
Rust source build on Windows and requires Visual Studio C++ Build Tools. Its
specific release date is allowed through the otherwise seven-day quarantine.
This version is explicitly pinned, not left floating, and must pass the bank's
SCA review before distribution.

The agent works in its dedicated `agent-workspace` directory. It supports the
bank's native Gemini connector directly or Groq's OpenAI-compatible API.

## Prerequisites

- Python 3.12 or later, managed by `uv`

## Configure it

Enter keys in [`.env`](.env), which is ignored by Git:

```text
BANK_AGENT_PROVIDER=gemini
BANK_AGENT_GEMINI_API_KEY=your-key
BANK_AGENT_GEMINI_BASE_URL=https://connector-host/api/connectors/connector-id
BANK_AGENT_GEMINI_VERTEX_PROJECT=your-project
BANK_AGENT_GEMINI_VERTEX_LOCATION=your-location
BANK_AGENT_GROQ_API_KEY=your-groq-key
BANK_AGENT_LLM_TIMEOUT_SECONDS=120
BANK_AGENT_LLM_NUM_RETRIES=1
```

Set `BANK_AGENT_PROVIDER` to the provider you want to use. Gemini calls the
configured connector directly; Node.js and the OpenCode harness are not used.

## Start it

For the normal desktop workflow, double-click [Open Bank Agent.cmd](Open%20Bank%20Agent.cmd).
It opens the terminal client and uses the provider selected in `.env`.

From PowerShell:

```powershell
Set-Location C:\Users\asher\git\bank-openhands-cli
.\bank-agent\scripts\start-bank-agent.ps1
```

To explicitly use Groq:

```powershell
.\bank-agent\scripts\start-bank-agent.ps1 -Provider groq
```

### Optional: run it from any IDE terminal

Install one user-level command after the harness is configured:

```powershell
.\bank-agent\scripts\install-bank-agent-command.ps1
```

Open a new IDE terminal afterwards. From any workspace, start the configured
harness with `bank-agent`, or resume a named session with
`bank-agent resume session1`. The installer writes only a small launcher under
`%LOCALAPPDATA%\BankAgent\bin` and adds that directory to the current user's
`PATH`; it does not copy configuration or secrets. If this harness is moved,
run the installer again with `-Force`.

When `bank-agent` is started without arguments from a folder inside exactly one
saved session workspace, it resumes that session automatically. If no workspace
matches, the normal session picker opens. If several sessions share a workspace,
the picker opens with a notice rather than guessing.

The launcher reads secrets only into its child process. It never starts a local
connector bridge.

## Offline bank bundle

The vendored `vendor/openhands-sdk` and `vendor/openhands-tools` folders make
the agent source self-contained. A release bundle also contains a local
`wheelhouse/` folder with all Windows/Python dependency wheels. It is excluded
from Git because it is a generated binary artifact.

The release copy is installed with the approved Python `pip`; `uv` is not
required on the bank workstation. The current wheelhouse is for 64-bit Windows
with CPython 3.13, so a different bank Python version requires a matching
wheelhouse. Every wheel still needs normal bank SCA approval. Run:

```powershell
.\bank-agent\scripts\setup-offline.ps1
```

The script installs only from `wheelhouse/`; it does not contact Artifactory or
the internet. `requirements-offline.txt` is the human-reviewable pinned package
manifest. Then create the bank-managed `.env` and use `Open Bank Agent.cmd`.

### Transferring an individual missing wheel

If Artifactory is missing a package, run `scripts/export-local-wheels.ps1` on
the development machine. It creates single-line Base64 files plus checksums in
`local-wheel-transfer/` for the currently known missing wheels. Copy that folder
and `scripts/import-local-wheels.ps1` to the bank machine, then run the import
script there. It restores verified `.whl` files into `local-wheels/`; install
with `pip --find-links .\\local-wheels -r requirements-offline.txt`.

To give the agent a task directly:

```powershell
.\bank-agent\scripts\start-bank-agent.ps1 -AgentArgs @("Explain this repository and add a concise README section for local development.")
```

## Local sessions

Each conversation is saved locally under `sessions/`, outside the agent workspace
and excluded from Git. Starting the client without a task opens a Windows session
picker: use Up/Down to select, Enter to resume, `n` for a new session, `/` to
search all sessions (including archived ones), `a` to archive or unarchive the
selection, and `d` to permanently delete its chat history after confirmation.
A second confirmation can also delete its named project workspace. A workspace
used by another active or archived session is kept, as are Scratch and paths
outside `agent-workspace/projects`.
Press `s` for settings; select **Cross-project/session harness instructions**
and press Enter to edit them in Windows Notepad. Press `h` to view
[HELP.txt](HELP.txt), which lists every menu, chat, and command-line command.
Choosing `n` asks for a local session name, then lets you select a folder under
`agent-workspace/projects/`, create a safely named project folder there, or use
`agent-workspace/scratch/` for temporary work. **Create new workspace** is
selected by default, and pressing `n` from this picker creates one directly.
Scratch contents are deliberately cleared every time a Scratch session starts,
including when an older Scratch session is resumed. It is therefore intended
only for work you do not want treated as a project. Copy anything worth keeping
into a project folder before you start Scratch again. Selecting an existing
project from a *new* session shows a prominent warning and requires a second
Enter, because its files are shared while its prior agent context is not. The
selected folder becomes that session's actual workspace and is restored when you
resume it. Session names must be unique case-insensitively, including archived
sessions. Workspace names must also be unique case-insensitively. The session
name is never sent to the model as a task.

Only one Bank Agent window may run a given session at a time. Opening that
session from the interactive picker leaves the picker open with a notice, so you
can select another session instead of risking concurrent writes. A direct
`resume` command still returns an error. A lock left by a crashed process is
reclaimed automatically when its process is gone.

Inside a chat, `/h` or `/help` shows [HELP.txt](HELP.txt). `/e` and `/exit`
close the current session cleanly and return to the session picker. `/q` and
`/quit` close Bank Agent while leaving the terminal open at a command prompt.

Use the same commands from PowerShell when a picker is not appropriate:

```powershell
bank-agent sessions
bank-agent sessions --archived
bank-agent sessions --all
bank-agent archive <session>
bank-agent unarchive <session>
bank-agent resume <session>
```

Archived sessions do not appear in the default list, but remain on disk and can
be found through `sessions --archived` or resumed by name. For `archive`,
`unarchive`, and `resume`, `<session>` accepts the unique session name
(case-insensitive), a full ID, or a unique ID prefix. Session history can
contain task text and tool output, so treat the local `sessions/` directory as
sensitive developer data. On each start or resume, the agent receives a compact
runtime note with the current local and UTC time, workspace, prior activity, and
its CLI run number; this note is system context, not a user message.

New session and workspace names cannot contain spaces. Use a hyphen, underscore,
or camelCase instead (for example, `debug-repo` or `debugRepo`).
Press Enter at the new-session name prompt to return to the session list without
creating one.

## Bank Agent instructions

Bank Agent has exactly two optional instruction files. They are local to this
harness; files named `AGENTS.md` inside cloned repositories are intentionally
ignored.

```text
agent-workspace/AGENTS.md
agent-workspace/projects/<project-name>/AGENTS.md
```

The first file provides general organization guidance. The second provides
guidance for one selected Bank Agent workspace. Both are loaded at session
start; the workspace file wins only where it explicitly conflicts with the
general file. Harness-enforced safety controls always take precedence. When you
create a new named project, the CLI shows an arrow selector for optional
workspace instructions. Select **Add or paste instructions in Notepad** and
press Enter, or press `p` to skip. In Notepad, add or paste instructions, save
with Ctrl+S, and close it to continue. Closing an empty file keeps the workspace
instruction-free, so it will prompt again next time. You can also edit either
file in an editor, or ask the agent to propose an approved edit. Changes take
effect when the next session run starts, including when an existing session is
resumed. Scratch has no project instruction file because its contents are cleared
before every run.

## Harness skills

Bank Agent loads local, cross-project skills from:

```text
agent-workspace/skills/<skill-name>/SKILL.md
```

Skills use the standard AgentSkills directory format. Their names and descriptions
are shown to the model at the start of every new or resumed session, while their
full instructions are loaded only when relevant. Invalid skills are skipped with
a warning instead of preventing Bank Agent from starting. Skills are local and
ignored by Git because they may contain organization-specific knowledge.

Bank Agent accepts instruction-only skills. A skill may describe terminal
commands for the agent to run through the normal approval policy, but embedded
inline commands and `.mcp.json` configurations are rejected.

The Settings screen includes a Skills browser. It lists every valid and invalid
local skill and shows its status, description, and file location. Guided skill
creation is temporarily unavailable while its behavior is evaluated with a
stronger model. Its implementation and existing draft sessions are preserved;
manually installed skills continue to load normally. See
[`docs/guided-skill-creation-status.md`](docs/guided-skill-creation-status.md)
for the observed problems and requirements for re-enabling it.

## Configuration

The defaults are intentionally local and conservative:

- `BANK_AGENT_PROVIDER` selects `gemini` or `groq`.
- `BANK_AGENT_MODEL` and `BANK_AGENT_API_KEY` are generic overrides.
- `BANK_AGENT_GEMINI_BASE_URL`, `BANK_AGENT_GEMINI_VERTEX_PROJECT`, and
  `BANK_AGENT_GEMINI_VERTEX_LOCATION` identify the native Gemini connector.
  Calls are translated in-process; no localhost bridge is used.
- `BANK_AGENT_GROQ_*` controls Groq; the wrapper adds the `groq/` prefix and
  uses Groq's default API endpoint unless a base URL is supplied.
- `BANK_AGENT_LLM_TIMEOUT_SECONDS` defaults to 120. An unresponsive model call
  times out instead of leaving the client working indefinitely; the default
  `BANK_AGENT_LLM_NUM_RETRIES=1` makes one retry before reporting the error.
- Direct file edits are limited to the selected session workspace.
- A small built-in allowlist permits safe read-only inspections automatically.

Persistent work must use a named project. Use
`--workspace agent-workspace/projects/my-project` to select one directly; without
`--workspace`, direct tasks use disposable Scratch.

## Approvals and local allowlist

All actions outside the read-only allowlist require explicit approval. When the
agent proposes one terminal command that stays within `agent-workspace`, the
prompt offers `y/N/a`:

- `y` approves it once.
- `n` (or Enter) rejects it.
- `a` approves it and saves that exact command to `policy/allowlist.json`.

The agent cannot write this local allowlist itself. `a` saves the exact command,
including clone, build, commit, push, or API commands if you explicitly choose
it. Shell-chained commands and commands with obvious paths outside
`agent-workspace` never offer `a`.

## Scope

This first version intentionally has no web UI, server process, Git hosting
integration, Splunk integration, automatic approval, or diff view. Git and
other installed command-line tools remain available to the agent through the
terminal tool, subject to approval.
