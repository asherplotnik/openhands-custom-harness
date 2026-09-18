# Guided skill creation status

Status: temporarily unavailable as of 2026-09-12. The implementation and draft
data are preserved; the Skills browser and manually installed skills remain
available.

## Why it is shelved

Testing with `groq/openai/gpt-oss-120b` exposed behavior that is unsafe for
unsupervised skill authoring:

- The model repeatedly invented unavailable tool names such as
  `repo_browser.list_directory` after receiving the valid tool list.
- It proposed a skill before reading the requested source material.
- After reading the source, it still asserted nonexistent paths and commands,
  including `bank_agent/config/*.toml`, `/new`, and `/resume`.
- Structural validation can prove that a skill is well formed, but it cannot
  prove that its operational claims are accurate.
- The first search compatibility implementation could traverse session history
  and runtime configuration when searching the application root. Search now
  excludes private runtime data and broad generated/dependency directories.

The feature is gated by `GUIDED_SKILL_CREATION_ENABLED` in
`bank_agent/skills.py`. While it is false, the creation entry and shortcut are
hidden and preserved draft sessions cannot be opened through Bank Agent.

## Before re-enabling

1. Repeat the same source-grounded authoring scenario with Gemini 3.8 Flash.
2. Verify that the model uses only tools present in the supplied schema and
   recovers correctly from one rejected tool call.
3. Require the author to inspect the requested sources before proposing writes.
4. Decide how installation should surface evidence and unsupported references;
   folder/frontmatter validation alone is insufficient.
5. Test cancellation, resume, duplicate-name rejection, installation, automatic
   archival, and source-search boundaries end to end.
6. Update `README.md`, `HELP.txt`, and the UI tests when the feature is enabled.

The built-in `skill-creator`, isolated draft workspaces, `/install` validation,
and installation code should remain in place so testing can resume without
rebuilding the flow.
