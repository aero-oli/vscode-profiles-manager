---
name: vscode-profiles-manager
description: Use when the user wants to create, edit, audit, export, import, backup, restore, or maintain Visual Studio Code / VS Code Insiders profiles, profile-specific settings, keybindings, snippets, tasks, MCP server configuration, extensions, profile manifests, or workspace/profile associations across macOS, Windows, Linux, or portable/user-data-dir installs.
---

# VS Code Profiles Manager

Use this skill to help a user manage VS Code profiles safely and repeatably.

## Core model

VS Code profiles are sets of customisations used to switch editor configuration by workflow, project, demo, language, troubleshooting case, or machine. A profile can include settings, extensions, keyboard shortcuts, snippets, tasks, MCP servers, and UI layout state. The Default Profile is the normal user configuration; named profiles override selected parts of it.

Treat profiles as a layered system:

1. **Application / default user settings**: normal VS Code user configuration.
2. **Named profile settings**: profile-specific files under the VS Code User profiles directory.
3. **Workspace settings**: `.vscode/settings.json` or `.code-workspace` settings that override user/profile settings for a project.
4. **Extension state and UI state**: partly managed by VS Code internals; do not casually edit internal databases.

## High-level rules

- Prefer official VS Code mechanisms first: Profiles editor, `.code-profile` import/export, and the `code` CLI.
- Use direct file edits only for JSON/JSONC configuration files such as `settings.json`, `keybindings.json`, snippets, tasks, and workspace recommendations.
- Always make a timestamped backup before editing profile files.
- Validate JSON/JSONC after editing.
- Do not edit `state.vscdb`, `storage.json`, workspace storage, global storage, or profile/workspace association internals unless the user explicitly asks for a low-level repair and accepts that VS Code must be closed first.
- Do not delete profiles, uninstall extensions, or clear settings unless the user has explicitly asked for that destructive action.
- When the user wants inheritance, explain that VS Code can copy from another profile, but copied profiles are not live-linked to the source profile.
- When the user wants cross-machine portability, prefer profile export/import or Settings Sync. Warn that remote windows such as SSH, Dev Containers, and WSL have extension sync limitations.
- For isolated environment variables between VS Code instances, use separate `--user-data-dir` instances rather than normal profiles.

## Useful official commands

Create/open a profile:

```bash
code ~/some/workspace --profile "Profile Name"
```

If the named profile does not exist, VS Code creates an empty profile with that name.

Install an extension into a named profile:

```bash
code --install-extension publisher.extension --profile "Profile Name"
```

Uninstall an extension from a named profile:

```bash
code --uninstall-extension publisher.extension --profile "Profile Name"
```

List extensions in a named profile:

```bash
code --list-extensions --show-versions --profile "Profile Name"
```

Launch fully isolated VS Code state, extensions, environment, and UI:

```bash
code ~/some/workspace --user-data-dir ~/.vscode-data/some-isolated-profile
```

## Standard workflow

### 1. Clarify the target outcome only if necessary

Resolve these details from the user request or existing context:

- VS Code stable, Insiders, VSCodium, or a custom portable build.
- Profile name.
- Whether this is a new profile, edit to an existing profile, audit, backup, restore, migration, or cleanup.
- Whether changes should apply only to a profile, all profiles, or a workspace.
- Desired extension set and settings.

If the requested action is safe and specific enough, proceed without extra questioning.

### 2. Inspect the environment

Use the helper script when available:

```bash
python scripts/vscode_profile_manager.py paths --variant code
python scripts/vscode_profile_manager.py list-profiles --variant code
```

For Insiders:

```bash
python scripts/vscode_profile_manager.py paths --variant insiders
```

Manually check likely locations:

- macOS stable: `~/Library/Application Support/Code/User`
- macOS Insiders: `~/Library/Application Support/Code - Insiders/User`
- Windows stable: `%APPDATA%\Code\User`
- Windows Insiders: `%APPDATA%\Code - Insiders\User`
- Linux stable: `~/.config/Code/User`
- Linux Insiders: `~/.config/Code - Insiders/User`

Named profile files are stored under the `profiles` directory inside the relevant User directory. The profile ID is not always the same as the profile display name.

Use `list-profiles` before editing an existing named profile. It reports known profile IDs, display names when available, and the concrete `settings.json`, `keybindings.json`, `tasks.json`, and snippets paths. If the target profile name still cannot be mapped to an ID, ask the user to open that profile's Settings JSON from VS Code and use the opened file path as `settingsFile`.

### 3. Back up before mutation

Before any file edit:

```bash
python scripts/vscode_profile_manager.py backup --variant code --out ~/Desktop/vscode-profile-backups
```

For one profile folder when the profile ID is known:

```bash
python scripts/vscode_profile_manager.py backup --variant code --profile-id PROFILE_ID --out ~/Desktop/vscode-profile-backups
```

### 4. Create or open the profile

Use the CLI:

```bash
code ~/some/workspace --profile "Profile Name"
```

If no workspace is specified, create/use a temporary empty folder so the command is deterministic. Avoid assuming the last active workspace is suitable.

### 5. Manage extensions by profile

Install from an explicit list:

```bash
code --install-extension ms-python.python --profile "Python"
code --install-extension charliermarsh.ruff --profile "Python"
```

List current extensions:

```bash
code --list-extensions --show-versions --profile "Python"
```

For bulk operations, use the helper:

```bash
python scripts/vscode_profile_manager.py install-extensions --profile "Python" --extensions ms-python.python charliermarsh.ruff
```

### 6. Edit settings safely

If the profile settings path is known:

```bash
python scripts/vscode_profile_manager.py merge-settings --file "/path/to/settings.json" --set-json '{"editor.formatOnSave":true,"editor.defaultFormatter":"charliermarsh.ruff"}'
```

For JSON arrays like `keybindings.json`, edit manually or with a small script that preserves valid JSONC. Do not use a top-level object merge command on array files.

### 7. Maintain profile manifests

For repeatable profile setup, keep a manifest in version control or in a personal dotfiles folder. Use this structure for command generation and audit:

```json
{
  "profile": "Python",
  "variant": "code",
  "extensions": [
    "ms-python.python",
    "charliermarsh.ruff"
  ],
  "settings": {
    "editor.formatOnSave": true,
    "[python]": {
      "editor.defaultFormatter": "charliermarsh.ruff"
    }
  },
  "notes": "Purpose, assumptions, machine-specific exclusions."
}
```

Use manifests to reconstruct or audit profiles rather than relying on undocumented VS Code state.

To apply profile file changes from a manifest, include either:

- `profileId`: internal folder name from `list-profiles`
- `settingsFile`: exact path opened from VS Code's profile Settings JSON command

Without one of those targets, `apply-spec` must stop before creating/opening VS Code or installing extensions. Manifest fields for `settings`, `removeSettings`, `keybindings`, `tasks`, and `snippets` are profile-file changes and require a target.

### 8. Audit and repair

When debugging profile issues:

1. Check the active profile in the VS Code title bar / Manage gear / Profiles editor.
2. Use `@modified` in Settings UI to see changed settings.
3. Validate JSON/JSONC files for syntax errors.
4. List profile-specific extensions.
5. Temporarily open an Empty Profile to determine whether the issue is caused by an extension or setting.
6. If settings will not save, inspect `settings.json` for syntax errors.

## Common tasks

### Create a clean Python profile

```bash
mkdir -p ~/tmp/vscode-profile-bootstrap && code ~/tmp/vscode-profile-bootstrap --profile "Python"
code --install-extension ms-python.python --profile "Python"
code --install-extension ms-python.vscode-python-envs --profile "Python"
code --install-extension charliermarsh.ruff --profile "Python"
code --install-extension tamasfe.even-better-toml --profile "Python"
```

Then open the profile settings JSON from VS Code and apply:

```json
{
  "editor.formatOnSave": true,
  "python.analysis.autoImportCompletions": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  }
}
```

### Create a clean AI-agent coding profile

```bash
mkdir -p ~/tmp/vscode-profile-bootstrap && code ~/tmp/vscode-profile-bootstrap --profile "AI Agent Coding"
code --install-extension github.copilot --profile "AI Agent Coding"
code --install-extension github.copilot-chat --profile "AI Agent Coding"
code --install-extension ms-vscode-remote.remote-containers --profile "AI Agent Coding"
code --install-extension ms-vscode-remote.remote-ssh --profile "AI Agent Coding"
```

Then add only agent/tooling-specific settings that should not bleed into the default profile.

### Export/share a profile

Use VS Code Profiles editor → overflow menu on the profile → Export. Export either as a local `.code-profile` file or a GitHub gist. Prefer local `.code-profile` for personal backups; prefer a gist only when the user intentionally wants a share link.

### Import a profile

Use Profiles editor → New Profile dropdown → Import Profile. Import from a local `.code-profile` file or a gist URL, review the selected profile contents, then create/import.

## Helper scripts

This skill includes:

- `scripts/vscode_profile_manager.py`: safe helper for path/profile discovery, backups, JSONC validation, settings/profile-file writes, extension listing/installation/uninstallation, manifest dry-runs, and profile snapshots.
- `assets/example-profile-spec.json`: example manifest for repeatable profile setup.
- `assets/profile-spec.schema.json`: schema for profile manifests.
- `references/vscode-profiles-research.md`: grounded notes on VS Code profile behaviour and source links.

## Response style when using this skill

- Give the user one command at a time when they are actively troubleshooting.
- Be explicit about whether a command edits VS Code state or only reads it.
- Show exactly which file will be changed before changing it.
- Summarise completed changes by profile name, changed files, installed/uninstalled extensions, and backup location.
