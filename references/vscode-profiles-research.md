# VS Code Profiles Reference

Profile resolution and CLI behavior rechecked: 2026-10-11. Other notes last checked: 2026-09-06.

## Official behavior

- The Default Profile is the current normal user configuration.
- Named profiles can contain settings, keyboard shortcuts, snippets, tasks, extensions, MCP servers, and UI state/layout.
- When creating a profile, users can copy an existing profile/template or create an Empty Profile.
- A partial profile can omit entire configuration categories and use those categories live from the Default Profile.
- VS Code cannot inherit individual settings from another profile. Copying settings creates an independent copy with no live link.
- Selecting or creating a profile associates it with the current folder/workspace. Reopening that workspace activates its associated profile.
- `Developer: Reset Workspace Profiles Associations` resets associations without deleting profiles.
- Settings and extensions can be applied to all profiles through native UI actions.
- Temporary Profiles start empty and disappear after the VS Code session ends.
- Settings Sync supports settings, keyboard shortcuts, snippets, tasks, UI state, extensions, profiles, and MCP configuration. Extensions do not sync to or from remote SSH, Dev Container, or WSL windows.
- Profiles export to a local `.code-profile` file or an unlisted GitHub gist and import through the Profiles editor. Machine-scoped settings are excluded.
- `code <workspace> --profile <name>` opens the workspace with that profile and creates an Empty Profile if the name does not exist.
- `--profile` works with `--install-extension`, `--uninstall-extension`, and `--list-extensions`.
- `--user-data-dir` creates an isolated instance with separate environment variables, settings, extensions, and UI state.
- Named profile settings live at `User/profiles/<profile ID>/settings.json`; the file appears only after that profile overrides a setting.

## MCP boundaries

- User-profile MCP configuration is stored in that profile's `mcp.json`; workspace configuration is `.vscode/mcp.json`.
- Prefer `${env:...}` or `${input:...}` instead of literal API keys or passwords.
- MCP servers can execute arbitrary local code. Adding configuration must not imply trusting or starting the server.
- Starting a server directly from `mcp.json` can bypass the normal trust prompt, so use the MCP management UI for review and trust decisions.
- MCP enable/disable state is stored separately from `mcp.json`; a file-only backup cannot promise to restore it.

## Agent and helper design consequences

1. Read registration from `User/globalStorage/storage.json` (`userDataProfiles`) first. Use `User/sync/profiles/lastSyncprofiles.json` only when local metadata is unavailable. These are internal, read-only discovery sources; use the Profiles UI when metadata cannot establish identity.
2. Resolve display names to a direct-child profile ID, contain every file target, and use an exact internal ID only as an explicitly reported fallback.
3. Back up only documented JSON/JSONC configuration and snippets. Do not restore internal databases, extension state files, workspace associations, global storage, or UI state.
4. Capture extension versions with the official CLI as recovery evidence; use the CLI to change extensions.
5. Make creation/opening an explicit `open-profile` action. Supplying a workspace intentionally associates it; omitting one avoids manufacturing a bootstrap folder.
6. Refuse silent comment loss. Structured stdlib JSON writes cannot preserve JSONC comments, so require a targeted edit or explicit approval.
7. Restore archives defensively: require a helper manifest, reject traversal and symlinks, limit sizes, preview first, back up current files, and write atomically.

## Shared resources and restore

- `show-profile` uses `useDefaultFlags` to report effective paths; `snapshot` reads those resources. A profile backup stores files in its own directory and reports shared categories, so back up Default before any shared edit.
- For a change restricted to one profile, make its category independent in the Profiles editor. For an authorized shared settings change, use `backup --profile Default`, then `merge-settings --profile NAME --allow-shared-settings --set-json JSON`.
- Preview a helper archive with `restore --archive PATH`. Inspect its target, files, and identity before applying `restore --archive PATH --confirm`. Close VS Code first for a multi-file restore.
- Restore validates JSON/JSONC and snippet payloads before any mutation and backs up existing configuration. An empty configuration target needs no recovery archive; other backup failures stop the restore.
- Restore overlays archived files at their original paths. It does not remove extra files or recreate profiles, inheritance, associations, or extension state. Use native `.code-profile` import/export for transferring a profile to another installation.

## Primary sources

- VS Code Profiles: https://code.visualstudio.com/docs/configure/profiles
- VS Code Settings: https://code.visualstudio.com/docs/configure/settings
- VS Code CLI: https://code.visualstudio.com/docs/configure/command-line
- VS Code Settings Sync: https://code.visualstudio.com/docs/configure/settings-sync
- VS Code MCP servers: https://code.visualstudio.com/docs/agent-customization/mcp-servers
- VS Code Dev Containers: https://code.visualstudio.com/docs/devcontainers/create-dev-container
- VS Code profile registration: https://github.com/microsoft/vscode/blob/main/src/vs/platform/userDataProfile/node/userDataProfile.ts
- VS Code effective profile resources: https://github.com/microsoft/vscode/blob/main/src/vs/platform/userDataProfile/common/userDataProfile.ts
