# Bank Agent Task List

This file contains improvements we have explicitly decided to implement. Broader
ideas and possible future work belong in `suggested-task-list.md`.

## Prevent persistent approval for sensitive commands

- Maintain a harness-enforced list of commands that must never offer the `a`
  (remember approval) option.
- Start with remote-access commands:
  - `ssh`
  - `scp`
  - `sftp`
- Continue allowing deliberate one-time execution through `y` after showing the
  complete approval prompt.
- Match the executable safely even when it is invoked by absolute path or uses a
  platform-specific executable suffix such as `.exe`.
- Ignore or remove any previously stored exact-command approval when its
  executable becomes permanently non-rememberable.
- Add tests proving these commands always display `[y/N]`, never `[y/N/a]`, and
  cannot become auto-approved through the persistent command allowlist.

## Add an unrestricted sandbox mode

- Add an explicit launch mode for environments that are genuinely disposable
  and externally isolated, where the agent may operate without per-action
  approval prompts.
- Never enable this mode by default or infer it merely from a folder name.
- Require an unmistakable explicit opt-in at launch and display a persistent,
  prominent warning while it is active.
- Do not save unrestricted mode as a normal user preference; each new process
  must opt in deliberately.
- Record use of unrestricted mode in the session lifecycle metadata and readable
  session log.
- Clearly document which protections are bypassed, including terminal-command
  approvals and workspace write restrictions, before implementation.
- Add tests proving normal mode remains unchanged and unrestricted mode cannot be
  activated accidentally.
