# VS Code Profiles Manager Skill

A standalone Codex/Agent Skill for safe, repeatable Visual Studio Code profile maintenance.

It combines official VS Code profile and extension commands with a standard-library Python helper for discovery, manifests, atomic file edits, backups, restores, dry runs, and recovery.

## Install

Place this directory at `~/.agents/skills/vscode-profiles-manager`, or under a repository's `.agents/skills/` directory. Codex detects skill changes automatically; restart if it does not appear.

## Safety model

- Uses the official `code` CLI for profile creation/selection and extension changes.
- Never writes VS Code internal databases, UI state, extension state files, or workspace-association stores.
- Constrains profile IDs and archive members to their intended directories.
- Verifies profile name/ID mappings when available.
- Refuses silent JSONC comment loss.
- Writes configuration atomically.
- Requires explicit confirmation for removal fields.
- Creates automatic recovery archives around apply and restore operations.
- Attempts file and touched-extension rollback when manifest application fails.

Transactional manifests accept Marketplace extension IDs only, optionally pinned with `@version`. Use the direct extension command for an explicitly reviewed VSIX file.

Backups can contain `mcp.json`; treat them as sensitive even though literal secrets should not be stored there.

## Quick checks

```bash
python3 scripts/vscode_profile_manager.py doctor --variant code
python3 scripts/vscode_profile_manager.py list-profiles --variant code
python3 scripts/vscode_profile_manager.py validate-spec --spec assets/example-profile-spec.json
python3 scripts/vscode_profile_manager.py apply-spec --spec assets/example-profile-spec.json --dry-run
```

The example manifest intentionally has no `profileId`. Add the ID returned by `list-profiles`, or an exact `settingsFile` path opened by VS Code, before applying profile-file changes.

## Backup and restore

```bash
python3 scripts/vscode_profile_manager.py backup --profile "Python" --profile-id PROFILE_ID --out ~/Desktop/vscode-profile-backups
python3 scripts/vscode_profile_manager.py restore --archive /path/to/backup.zip
python3 scripts/vscode_profile_manager.py restore --archive /path/to/backup.zip --confirm
```

Restore previews by default and does not automatically reconcile installed extensions.

## Requirements

- Python 3.10+; no third-party Python packages.
- The matching VS Code CLI for extension operations and profile opening: `code`, `code-insiders`, `codium`, or `--code-bin`.

Use `--user-dir` for file-only work on an exact custom/portable User directory. Use `--user-data-dir` for an isolated VS Code instance root when profile or extension CLI operations are also required.
