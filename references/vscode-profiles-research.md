# VS Code Profiles Research Notes

Last reviewed: 2026-07-05

## Official VS Code behaviour

- VS Code Profiles are used to create sets of customisations and switch/share them.
- Profiles are managed primarily through the Profiles editor.
- A new profile can be created from a template, from an existing profile, or as an empty profile.
- Profile contents can include settings, keyboard shortcuts, MCP servers, snippets, tasks, extensions, and UI layout/configuration.
- Profiles are associated with folders/workspaces after selection, so reopening a workspace can reactivate its associated profile.
- Profiles can be switched via the Command Palette or Profiles editor.
- Settings and extensions can be applied to all profiles through VS Code UI actions.
- Settings Sync can sync profiles across machines when Profiles is enabled in Settings Sync, but extensions are not synced to/from remote windows such as SSH, Dev Containers, or WSL.
- Profiles can be exported to a GitHub gist or a local `.code-profile` file, and imported from either.
- The CLI can open a workspace with `--profile "Profile Name"`; if the profile does not exist, VS Code creates an empty profile with that name.
- The CLI supports `--install-extension`, `--uninstall-extension`, and `--list-extensions` with `--profile`.
- VS Code user settings are JSON files. Profile settings live under a profile-ID folder inside the User `profiles` directory, and are only created when profile-specific settings exist.
- Profiles are stored under the User configuration directory:
  - Windows: `%APPDATA%\Code\User\profiles`
  - macOS: `$HOME/Library/Application Support/Code/User/profiles`
  - Linux: `$HOME/.config/Code/User/profiles`
  - Insiders uses `Code - Insiders` as the intermediate application folder.
- VS Code does not currently support live inheritance between profiles. Creating a profile from another profile copies settings but does not keep them linked.
- Machine-specific settings are not exported in profile exports.
- To isolate environment variables between VS Code instances, use `--user-data-dir`; this creates separate environment, settings, installed extensions, UI state and layout. Extensions must be installed separately for each user data directory.

## Practical implications for agents

1. **Prefer official surfaces.** Use `code --profile`, profile export/import, and extension CLI operations before touching files directly.
2. **Use manifests for maintainability.** A simple manifest of profile name, extensions, settings, and notes is easier to audit and recreate than internal VS Code state.
3. **Do not assume display name == profile ID.** Profile folders use internal IDs. Ask the user to open profile settings JSON or inspect profile folders when you need the exact file path.
4. **Avoid internal stores.** `state.vscdb`, `storage.json`, global storage, and workspace storage are implementation details. Edit them only as a last-resort repair with VS Code closed and a backup.
5. **Separate profiles from full isolation.** Profiles are good for editor customisations. Use `--user-data-dir` for fully isolated app state or different inherited environment variables.

## Sources

- VS Code Profiles documentation: https://code.visualstudio.com/docs/configure/profiles
- VS Code Settings documentation: https://code.visualstudio.com/docs/configure/settings
- VS Code CLI documentation: https://code.visualstudio.com/docs/configure/command-line
- VS Code Extensions documentation: https://code.visualstudio.com/docs/configure/extensions/extension-marketplace
- VS Code Terminal advanced documentation: https://code.visualstudio.com/docs/terminal/advanced
- OpenAI Codex skills documentation: https://developers.openai.com/codex/skills
