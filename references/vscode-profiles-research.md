# VS Code Profiles Reference

Last verified against online documentation: 2026-09-06

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

1. Treat the name-to-ID mapping read from `User/sync/profiles/lastSyncprofiles.json` as best-effort, read-only discovery because it is not a documented public interface.
2. Resolve display names to a direct-child profile ID, contain every file target, and use an exact internal ID only as an explicitly reported fallback.
3. Back up only documented JSON/JSONC configuration and snippets. Do not restore internal databases, extension state files, workspace associations, global storage, or UI state.
4. Capture extension versions with the official CLI as recovery evidence; use the CLI to change extensions.
5. Make creation/opening an explicit `open-profile` action. Supplying a workspace intentionally associates it; omitting one avoids manufacturing a bootstrap folder.
6. Refuse silent comment loss. Structured stdlib JSON writes cannot preserve JSONC comments, so require a targeted edit or explicit approval.
7. Restore archives defensively: require a helper manifest, reject traversal and symlinks, limit sizes, preview first, back up current files, and write atomically.

## Existing alternatives

- Native VS Code Profiles and Settings Sync are the primary interactive solution.
- Nix Home Manager offers declarative `programs.vscode.profiles.<name>` settings, extensions, keybindings, snippets, tasks, and MCP configuration for Nix-managed systems.
- Dev Containers are a better fit for repository-controlled runtimes and remote extension installation.
- Extension Profiles 3000 is complementary when multiple composable extension groups per workspace are more useful than a single native profile.
- Microsoft's `jsonc-parser` package is the preferred optional foundation if this helper later adopts comment-preserving targeted edits.

## Primary sources

- VS Code Profiles: https://code.visualstudio.com/docs/configure/profiles
- VS Code Settings: https://code.visualstudio.com/docs/configure/settings
- VS Code CLI: https://code.visualstudio.com/docs/configure/command-line
- VS Code Settings Sync: https://code.visualstudio.com/docs/configure/settings-sync
- VS Code MCP servers: https://code.visualstudio.com/docs/agent-customization/mcp-servers
- VS Code Dev Containers: https://code.visualstudio.com/docs/devcontainers/create-dev-container
- Home Manager VS Code options: https://home-manager.dev/manual/unstable/options/home-manager/programs/vscode.html
- Microsoft JSONC parser: https://github.com/microsoft/node-jsonc-parser
