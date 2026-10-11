# VS Code Profiles Manager Plugin

A Codex plugin for directly managing VS Code profiles from natural-language requests. It packages the existing agent skill and Python helper in the portable Agent Plugins format.

The agent discovers profiles by display name, uses official VS Code commands where available, safely edits supported profile files, manages extensions, and retains focused backup and restore tooling. Users do not need to create manifests or know VS Code's internal profile IDs.

## Typical use

Ask the agent to:

- create or open a profile;
- add or update settings and extensions;
- edit keybindings, tasks, snippets, or MCP configuration;
- inspect, troubleshoot, back up, or restore a profile; or
- guide or drive VS Code's Profiles UI for rename, icon, layout, import/export, deletion, and associations.

Clear requests proceed with the authorization already given. The agent asks when a target or consequence remains unresolved and previews restores before applying them.

## Install in Codex

From this repository, register its local marketplace and install the plugin:

```bash
codex plugin marketplace add .
codex plugin add vscode-profiles-manager@vscode-profiles-tools
```

Start a new chat after installation. Explicitly invoke the packaged skill with `$vscode-profiles-manager:vscode-profiles-manager`, or ask to manage a VS Code profile.

The marketplace at `.agents/plugins/marketplace.json` points to this repository's plugin root. After changing the local source, reinstall the plugin to refresh its cached copy. If your client does not offer CLI installation, restart the desktop app and install **VS Code Profiles Manager** from the **VS Code Profiles Tools** source in the Plugins Directory.

This plugin uses local Python and VS Code tools; it has no MCP server or account connection. It requires a client with local execution access to the VS Code installation.

To install just the standalone skill, copy `skills/vscode-profiles-manager/` into an agent skills directory such as `~/.agents/skills/vscode-profiles-manager`.

## Package layout

```text
plugin.json
.agents/plugins/marketplace.json
skills/vscode-profiles-manager/
  SKILL.md
  agents/openai.yaml
  references/vscode-profiles-research.md
  scripts/vscode_profile_manager.py
tests/
```

## Helper

```bash
profile_helper="skills/vscode-profiles-manager/scripts/vscode_profile_manager.py"
python3 "$profile_helper" doctor
python3 "$profile_helper" list-profiles
python3 "$profile_helper" show-profile --profile "Python"
python3 "$profile_helper" backup --profile "Python"
python3 "$profile_helper" merge-settings --profile "Python" --set-json '{"editor.formatOnSave":true}'
python3 "$profile_helper" snapshot --profile "Python"
```

Run `python3 "$profile_helper" --help` for the complete command list. These examples run from the repository root; installed skills resolve the helper relative to their own skill directory. The helper requires Python 3.10+ and uses no third-party packages. Extension and profile-opening operations require `code`, `code-insiders`, `codium`, or an explicit `--code-bin`.

Discovery reads local profile registration first, with Settings Sync metadata as a fallback. Shared Default categories are identified by `show-profile`; shared settings edits require `--allow-shared-settings`.

Backups include supported profile-owned configuration and extension snapshots. Back up Default separately before changing shared resources. Restore previews by default, validates payloads before writing, and stops if a required recovery backup fails. It restores archived files only, leaving extra files in place; it does not recreate profile registration, inheritance, UI state, or extensions.

Run `python3 -m unittest discover -s tests` for focused helper tests. [Skill evaluation cases](tests/skill-evals.md) cover selection and agent workflows.
