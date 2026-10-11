# Skill evaluation cases

Run each case in a fresh agent context with temporary profile fixtures and a stub VS Code CLI. Compare with the previous skill version; inspect the actions and resulting files, not just the final response. Do not mutate live profiles during these evaluations.

| Request / starting condition | Expected behavior |
| --- | --- |
| Update my Python profile; Settings Sync is disabled. | Resolve its name from local registration, back up, and change only the requested settings. |
| Change this profile's keybinding; it shares keybindings with Default. | Identify the shared resource and resolve the intended scope before editing it. |
| Create an empty Rust profile and install these extensions. | Create before resolving files, then re-list extensions to verify installation. |
| Restore this backup; one payload is malformed or recovery backup fails. | Preview safely; reject invalid payloads or failed backups before any file changes. |
| Edit this project's `.vscode/settings.json`. | Do not implicitly select this profile-management skill. |
