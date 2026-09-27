---
name: bank-agent-guide
description: Answer and troubleshoot questions about the Bank Agent harness, including its CLI, sessions, workspaces, instructions, skills, providers, persistence, approvals, and startup behavior. Do not use for code inside user project repositories or for generic OpenHands SDK questions.
---

# Bank Agent guide

Answer from the current Bank Agent installation rather than memory. This skill
is about the harness itself, not the repositories a developer places in a
session workspace.

## Find the current source

The invocation footer gives this skill's installed directory. From that
directory, ascend through `skills/` and `agent-workspace/`; the next parent is
the Bank Agent installation root. Do not assume the conversation workspace is
the harness source tree.

Read [references/source-map.md](references/source-map.md) when deciding which
source files establish a behavior. Use the exact available tool names without
inventing namespaces. Prefer targeted `read_file` calls; use `search` only with
a narrow query and an explicit source path.

## Answer accurately

1. Identify whether the question concerns documented user behavior,
   implementation details, local configuration, or the underlying OpenHands
   integration.
2. Inspect the current relevant documentation and implementation before
   answering anything version-dependent. Treat implementation as authoritative
   when documentation and code disagree, and call out the discrepancy.
3. Distinguish chat commands, picker keys, command-line subcommands, and startup
   script arguments. Never convert one category into another.
4. Cite relative source paths and useful function or class names. Give line
   numbers only when they were read from the current file; do not preserve line
   numbers as static knowledge.
5. State uncertainty when the source does not establish a claim. Do not invent
   paths, commands, storage formats, integrations, or guarantees.

For configuration questions, explain variable names, precedence, and validation
without revealing secret values. Do not read or quote `.env`, persisted session
events, allowlist contents, or other private runtime data merely to answer a
general harness question.

OpenHands is an implementation dependency. Inspect vendored OpenHands code only
when a Bank Agent behavior cannot be explained from the harness or when the user
specifically asks about that boundary. Do not present generic OpenHands
capabilities as Bank Agent features unless Bank Agent exposes them.

Questions are read-only by default. Do not modify harness code, configuration,
sessions, projects, or skills unless the user explicitly requests that change.
