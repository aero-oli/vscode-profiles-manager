---
name: vscode-profiles-manager
description: Create, inspect, edit, troubleshoot, back up, restore, and maintain Visual Studio Code, VS Code Insiders, or VSCodium profiles. Use for profile settings, extensions, keybindings, snippets, tasks, MCP configuration, import/export, workspace associations, or isolated user-data directories.
---

# VS Code Profiles Manager

Manage profiles from the user's requested outcome. Do not make the user supply manifests, internal profile IDs, file paths, or a fixed sequence of commands.

## Workflow

1. Infer the VS Code variant and profile name from the request. Run `doctor` only when the installation or variant is unclear.
2. Resolve an existing target with `show-profile --profile NAME`. Display names are the normal interface; an exact internal ID is only a fallback when no name mapping exists.
3. For a clear create, add, or update request, proceed without an extra approval. Before changing profile files, create a focused `backup --profile NAME`.
4. Ask immediately before deleting a profile, uninstalling extensions, removing configuration, or applying a restore. Preview restores before confirmation.
5. Validate every edited JSON/JSONC file and report the profile, changed files, extension actions, backup path, and any remaining UI step.

Use `python3 scripts/vscode_profile_manager.py ...` on macOS/Linux. On Windows use `py -3` or the available Python launcher. The helper uses only the standard library.

## Choose the right VS Code surface

- Use `open-profile --profile NAME [--workspace PATH]` to create or open a profile through the official VS Code CLI. A workspace is optional; supplying one also associates it with the profile.
- Use the extension commands for listing, installing, or uninstalling extensions in a named profile.
- Use `show-profile` to obtain the exact settings, keybindings, tasks, snippets, and MCP paths. Back up the profile, make a targeted edit, then run `validate` on every changed JSON/JSONC file.
- Use `merge-settings --profile NAME --set-json JSON` for straightforward top-level setting additions or replacements. Use `--strategy deep` only when retaining unspecified nested object members is intentional.
- Use the Profiles editor for rename, icon, included content categories, UI layout/state, **Use for New Windows**, deletion, import/export, and viewing or resetting workspace associations. Drive the UI when UI automation is available; otherwise give the user the shortest exact UI step.
- Use a separate `--user-data-dir` when the user needs a fully isolated VS Code instance. `--user-dir` is file-only and cannot safely target CLI extension/profile operations.

## File-editing boundaries

Supported profile content includes `settings.json`, `keybindings.json`, `tasks.json`, files under `snippets/`, and `mcp.json`.

- Preserve unrelated entries and the file's existing style. Prefer a targeted patch when a JSONC file contains comments; do not opt into comment loss without approval.
- Write only inside the resolved VS Code User/profile directory. Never infer a profile folder from its display name.
- Do not directly edit `state.vscdb`, `storage.json`, `extensions.json`, `globalStorage`, `workspaceStorage`, UI state, or workspace-association internals.
- Treat backups, snapshots, and MCP configuration as potentially sensitive. Prefer `${env:...}` or `${input:...}` references over literal credentials.
- Adding an MCP server does not authorize trusting or starting it. Leave trust and start decisions to the user in VS Code.

## Common commands

```bash
python3 scripts/vscode_profile_manager.py list-profiles
python3 scripts/vscode_profile_manager.py show-profile --profile "Python"
python3 scripts/vscode_profile_manager.py open-profile --profile "Rust"
python3 scripts/vscode_profile_manager.py backup --profile "Python"
python3 scripts/vscode_profile_manager.py merge-settings --profile "Python" --set-json '{"editor.formatOnSave":true}'
python3 scripts/vscode_profile_manager.py list-extensions --profile "Python" --show-versions
python3 scripts/vscode_profile_manager.py install-extensions --profile "Python" --extensions ms-python.python charliermarsh.ruff
python3 scripts/vscode_profile_manager.py snapshot --profile "Python"
```

For backup restoration, isolated installations, Settings Sync, partial-profile inheritance, remote windows, MCP trust, or current VS Code limitations, read [references/vscode-profiles-research.md](references/vscode-profiles-research.md).
