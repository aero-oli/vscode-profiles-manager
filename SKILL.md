---
name: vscode-profiles-manager
description: Manage Visual Studio Code, VS Code Insiders, and VSCodium profiles, including profile settings, extensions, creation, backups, and restores. Use when the request targets a VS Code profile or isolated instance; exclude general code editing and workspace-only configuration.
---

# VS Code Profiles Manager

Manage profiles from the user's requested outcome. Do not make the user supply manifests, internal profile IDs, file paths, or a fixed sequence of commands.

## Workflow

1. Establish the variant, instance, profile, and operation from the request. Run `doctor` when the installation is unclear.
2. For an existing profile, use `show-profile --profile NAME`. It reports effective paths and `sharedWithDefault`. If name resolution fails, inspect the Profiles UI before declaring the profile missing; do not ask the user to find internal IDs.
3. For creation, use `open-profile` for an Empty Profile, or the Profiles UI to copy a profile/template and select content. Do not require the new profile to exist first.
4. Before changes, back up affected configuration. Use `backup --profile NAME` for profile-owned files; back up Default separately for shared resources. Preserve explicit authorization already given; ask only when the target, scope, or consequences remain unresolved. Preview restores before applying them.
5. Verify the outcome: validate changed JSON/JSONC files, re-list extensions after extension changes, and check creation or UI changes in VS Code. Report the profile, changes, backup path, and anything awaiting UI verification; a successful file write alone does not establish active behavior.

The helper requires Python 3.10+ and uses only the standard library. Resolve `profile_helper` to the absolute `scripts/vscode_profile_manager.py` path relative to this skill directory; the working directory may be elsewhere. Use `python3` on macOS/Linux or `py -3` on Windows. CLI operations require `code`, `code-insiders`, `codium`, or `--code-bin`.

## Choose the right VS Code surface

- Use `open-profile --profile NAME [--workspace PATH]` to create or open a profile through the official VS Code CLI. A workspace is optional; supply an existing folder or `.code-workspace` file to associate it with the profile.
- Use the extension commands for listing, installing, or uninstalling extensions in a named profile.
- If a category is shared with Default, resolve whether to change Default or make the category independent through the Profiles UI. `merge-settings` requires `--allow-shared-settings` to change shared Default settings; this affects other sharing profiles too.
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
profile_helper="/path/to/vscode-profiles-manager/scripts/vscode_profile_manager.py"
python3 "$profile_helper" list-profiles
python3 "$profile_helper" show-profile --profile "Python"
python3 "$profile_helper" open-profile --profile "Rust"
python3 "$profile_helper" backup --profile "Python"
python3 "$profile_helper" merge-settings --profile "Python" --set-json '{"editor.formatOnSave":true}'
python3 "$profile_helper" list-extensions --profile "Python" --show-versions
python3 "$profile_helper" install-extensions --profile "Python" --extensions ms-python.python charliermarsh.ruff
```

For backup restoration, isolated installations, Settings Sync, partial-profile inheritance, remote windows, MCP trust, or current VS Code limitations, read [references/vscode-profiles-research.md](references/vscode-profiles-research.md).
