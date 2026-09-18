---
name: skill-creator
description: Create a reusable Bank Agent skill from a user's desired capability or maintained source material.
---

# Skill creator

Create exactly one skill as a direct child of the current draft workspace. The
result must have a required `SKILL.md`; add `references/`, `scripts/`, or
`assets/` only when they provide concrete value.

- Clarify the intended capability, when the skill should apply, and any source
  material only when the user's description leaves a material gap.
- Use a short folder and frontmatter name containing only lowercase letters,
  digits, and hyphens. The folder and frontmatter names must match.
- Make the description concise and specific enough for reliable discovery.
- Keep common guidance in `SKILL.md`; link substantial conditional detail from
  `references/` so it is loaded only when needed.
- Preserve the user's authorization boundaries. A skill may explain commands
  the agent should run through the normal terminal approval flow, but it must
  not use executable inline-command syntax or include `.mcp.json`.
- Do not initialize Git, clone repositories, install the draft, or edit the live
  skill catalog. Installation belongs to the harness after explicit user
  confirmation.

Inspect the completed files for placeholders and internal contradictions. Tell
the user what was created and ask them to run `/install` when the draft is ready.
