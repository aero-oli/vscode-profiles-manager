---
name: vscode-profiles-manager
description: Safely create, inspect, audit, back up, restore, and maintain Visual Studio Code, VS Code Insiders, or VSCodium profiles. Use for profile-specific settings, extensions, keybindings, snippets, tasks, MCP configuration, repeatable manifests, workspace associations, Settings Sync/export guidance, or isolated user-data-dir instances on macOS, Windows, and Linux.
---

# VS Code Profiles Manager

Manage profiles through official VS Code surfaces first, and use the bundled helper for deterministic discovery, backups, validation, manifests, and recoverable file changes.

## Model and boundaries

- The Default Profile contains normal user configuration.
- A named profile can contain settings, extensions, keyboard shortcuts, snippets, tasks, MCP servers, and UI state.
- A partial profile can inherit entire omitted configuration categories from the Default Profile. Copied settings are not live-linked, and VS Code does not support per-setting inheritance from another profile.
- Workspace and workspace-folder settings override user/profile settings according to normal VS Code precedence.
- Selecting or opening a profile for a folder associates that folder with the profile.
- Use `--user-data-dir` when separate environment variables, settings, extensions, and UI state are required. A normal profile is not a fully isolated instance.

Never directly edit `state.vscdb`, `storage.json`, `globalStorage`, `workspaceStorage`, `extensions.json`, UI state, or workspace-association internals. Use the Profiles editor, Settings Sync, Command Palette, and `code` CLI for those resources.

## Safety rules

1. Inspect and resolve the exact variant, User directory, profile display name, and profile ID before writing.
2. Run `validate-spec` and `apply-spec --dry-run` before applying a manifest.
3. Let `apply-spec` create its automatic recovery archive. Do not bypass profile identity checks unless an exact Settings JSON path opened by VS Code proves the target.
4. Require explicit user intent and `--confirm-destructive` for extension, setting, or MCP removals.
5. The helper refuses to rewrite commented JSONC by default because a structured rewrite would remove comments. Prefer VS Code or a targeted patch. Use `--allow-comment-loss` only after showing the dry-run diff and receiving explicit approval.
6. Close VS Code before a multi-file restore. Preview `restore` before passing `--confirm`.
7. Treat backups and snapshots as potentially sensitive. `mcp.json` can contain credentials; prefer `${env:...}` or `${input:...}` variables and do not publish backup archives or secret gists.
8. Never start or trust a newly added MCP server on the user's behalf. Review it in VS Code and let the user accept the trust prompt.

## Choose the Python launcher

Use `python3` on macOS/Linux. On Windows, use `py -3` or `python` according to the installed launcher. The examples below use `python3`.

Set a short shell variable only when it improves repeated commands:

```bash
python3 scripts/vscode_profile_manager.py doctor --variant code
```

The helper uses only Python's standard library. Profile extension operations additionally require the official VS Code CLI (`code`, `code-insiders`, or `codium`) on `PATH`, or an explicit `--code-bin`.

## Inspect first

Run read-only preflight and discovery:

```bash
python3 scripts/vscode_profile_manager.py doctor --variant code
python3 scripts/vscode_profile_manager.py paths --variant code
python3 scripts/vscode_profile_manager.py list-profiles --variant code
```

Important locations:

- macOS stable: `~/Library/Application Support/Code/User`
- macOS Insiders: `~/Library/Application Support/Code - Insiders/User`
- Windows stable: `%APPDATA%\Code\User`
- Windows Insiders: `%APPDATA%\Code - Insiders\User`
- Linux stable: `~/.config/Code/User`
- Linux Insiders: `~/.config/Code - Insiders/User`

Use `--user-dir` for file-only inspection, backup, restore, or edits of an exact custom/portable User folder. The VS Code CLI has no matching `--user-dir` option, so the helper rejects CLI-scoped actions for a non-default custom User folder and skips misleading extension snapshots. Use `--user-data-dir` for an isolated-instance root; the helper uses its `User` child and passes the same option to the CLI.

`list-profiles` maps names from a best-effort, read-only Settings Sync cache. Profile folder IDs are internal and need not equal display names. If a name cannot be verified, open that profile's Settings JSON in VS Code and use the exact path as `settingsFile` in the manifest.

## Create, open, and associate profiles

Preview, then open a workspace with a profile through the official CLI:

```bash
python3 scripts/vscode_profile_manager.py open-profile --profile "Python" --workspace ~/projects/example --dry-run
python3 scripts/vscode_profile_manager.py open-profile --profile "Python" --workspace ~/projects/example
```

If the profile does not exist, VS Code creates an Empty Profile. Opening the folder with the profile associates them. `apply-spec` deliberately does not create a profile, open a GUI, or change workspace associations.

Use the Profiles editor for profile renaming, icons, content categories, previews, deletion, **Use for New Windows**, and viewing associations. Use `Developer: Reset Workspace Profiles Associations` to reset all associations without deleting profiles.

For shared settings or extensions, prefer VS Code's **Apply Setting to all Profiles** and **Apply Extension to all Profiles** actions.

## Back up and restore

Back up one verified profile, including safe JSON/JSONC files, snippets, and an extension snapshot:

```bash
python3 scripts/vscode_profile_manager.py backup --variant code --profile "Python" --profile-id PROFILE_ID --out ~/Desktop/vscode-profile-backups
```

Omit `--profile-id` for Default Profile configuration plus all named-profile safe files. Backups intentionally exclude VS Code internal databases and state files. Archives are created with owner-only permissions and unique timestamps.

Preview a restore:

```bash
python3 scripts/vscode_profile_manager.py restore --variant code --archive /path/to/backup.zip
```

After reviewing the targets and closing VS Code:

```bash
python3 scripts/vscode_profile_manager.py restore --variant code --archive /path/to/backup.zip --confirm
```

Restore creates a pre-restore recovery archive, rejects traversal/symlink/oversized members, writes files atomically, and rolls back file writes on failure. It does not reconcile installed extensions automatically; use the archived extension snapshot as recovery evidence and manage extensions through the CLI or UI.

For portable sharing or full profile migration, prefer the Profiles editor's local `.code-profile` export/import. Use a GitHub gist only when the user intentionally wants an unlisted share link. Machine-scoped settings are not exported.

## Manage extensions

Read-only listing:

```bash
python3 scripts/vscode_profile_manager.py list-extensions --profile "Python" --show-versions
```

Install an explicit set:

```bash
python3 scripts/vscode_profile_manager.py install-extensions --profile "Python" --extensions ms-python.python charliermarsh.ruff
```

The direct install command can also pass an explicitly reviewed `.vsix` path to VS Code. Manifests accept Marketplace IDs only (optionally `publisher.extension@version`) because a VSIX path cannot be identified reliably enough for transactional rollback.

Uninstall only after explicit user authorization:

```bash
python3 scripts/vscode_profile_manager.py uninstall-extensions --profile "Python" --extensions publisher.extension
```

Remote windows such as SSH, Dev Containers, and WSL do not sync extensions to or from the local window. Prefer `.vscode/extensions.json` recommendations or `devcontainer.json` customizations for repository-controlled remote tooling.

## Edit one settings file

Preview a top-level replacement merge:

```bash
python3 scripts/vscode_profile_manager.py merge-settings --file "/path/to/settings.json" --set-json '{"editor.formatOnSave":true}' --dry-run
```

Then apply it:

```bash
python3 scripts/vscode_profile_manager.py merge-settings --file "/path/to/settings.json" --set-json '{"editor.formatOnSave":true}'
```

Object-valued settings are replaced by default, matching top-level setting semantics. Use `--strategy deep` only when retaining unspecified nested members is intentional. Every write is atomic and makes an adjacent timestamped `.bak` copy when the file already exists.

## Use repeatable manifests

Start from `assets/example-profile-spec.json` or scaffold a spec; scaffolding copies the JSON Schema beside the output file:

```bash
python3 scripts/vscode_profile_manager.py scaffold-spec --profile "Python" --out ~/profiles/python.json
```

Profile-file fields require `profileId` from `list-profiles` or an exact profile `settingsFile`. Supported fields include Marketplace extension IDs/removals, settings and merge strategy, keybindings, tasks, snippets, and MCP server additions/removals.

Validate and preview:

```bash
python3 scripts/vscode_profile_manager.py validate-spec --spec ~/profiles/python.json
python3 scripts/vscode_profile_manager.py apply-spec --spec ~/profiles/python.json --dry-run
```

Apply non-destructive changes:

```bash
python3 scripts/vscode_profile_manager.py apply-spec --spec ~/profiles/python.json
```

After explicit approval for removal fields:

```bash
python3 scripts/vscode_profile_manager.py apply-spec --spec ~/profiles/python.json --confirm-destructive
```

`apply-spec` validates the complete manifest before mutation, verifies profile identity, creates a recovery archive, writes files atomically, applies extension changes fail-fast, and attempts file and touched-extension rollback if any step fails.

MCP manifest changes merge only the named entries under `mcp.json`'s `servers` object. They never start a server or modify its separately stored enabled/trust state. After applying, review the server in the MCP configuration editor and let the user decide whether to trust or start it.

## Diagnose profile problems

1. Confirm the current profile in the title bar, Manage button, or Profiles editor.
2. Use `@modified` in Settings to inspect overrides.
3. Run `validate` on relevant JSON/JSONC files.
4. Run `snapshot` for settings, tasks, keybindings, snippets, and extension versions. Include MCP content only with explicit `--include-mcp` because it may be sensitive.
5. Open an Empty or Temporary Profile to distinguish core behavior from extensions/settings.
6. Use Settings Sync's **Show Synced Data** and local backup views for sync recovery.

Read `references/vscode-profiles-research.md` when current behavior, inheritance, MCP trust, Settings Sync, export, remote windows, or competing declarative tools affect the task.

## Reporting

State whether each command was read-only or mutating. Before mutation, show the resolved profile name, ID, files, extension actions, and backup destination. After completion, report changed files, installed/uninstalled extensions, recovery archive, validation results, and any rollback warnings.
