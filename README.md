# VS Code Profiles Manager Skill

A Codex/Agent Skill for directly managing VS Code profiles from natural-language requests.

The agent discovers profiles by display name, uses official VS Code commands where available, safely edits supported profile files, manages extensions, and retains focused backup and restore tooling. Users do not need to create manifests or know VS Code's internal profile IDs.

## Typical use

Ask the agent to:

- create or open a profile;
- add or update settings and extensions;
- edit keybindings, tasks, snippets, or MCP configuration;
- inspect, troubleshoot, back up, or restore a profile; or
- guide or drive VS Code's Profiles UI for rename, icon, layout, import/export, deletion, and associations.

Clear requests proceed with the authorization already given. The agent asks when a target or consequence remains unresolved and previews restores before applying them.

## Helper

```bash
python3 scripts/vscode_profile_manager.py doctor
python3 scripts/vscode_profile_manager.py list-profiles
python3 scripts/vscode_profile_manager.py show-profile --profile "Python"
python3 scripts/vscode_profile_manager.py backup --profile "Python"
python3 scripts/vscode_profile_manager.py merge-settings --profile "Python" --set-json '{"editor.formatOnSave":true}'
python3 scripts/vscode_profile_manager.py snapshot --profile "Python"
```

Run `python3 scripts/vscode_profile_manager.py --help` for the complete command list. The helper requires Python 3.10+ and uses no third-party packages. Extension and profile-opening operations require `code`, `code-insiders`, `codium`, or an explicit `--code-bin`.

Discovery reads local profile registration first, with Settings Sync metadata as a fallback. Shared Default categories are identified by `show-profile`; shared settings edits require `--allow-shared-settings`.

Backups include supported profile-owned configuration and extension snapshots. Back up Default separately before changing shared resources. Restore previews by default, validates payloads before writing, and stops if a required recovery backup fails. It restores archived files only, leaving extra files in place; it does not recreate profile registration, inheritance, UI state, or extensions.

Run `python3 -m unittest discover -s tests` for focused helper tests. [Skill evaluation cases](tests/skill-evals.md) cover selection and agent workflows.

Install this directory under an agent skills directory such as `~/.agents/skills/vscode-profiles-manager`, or use it as a repository-local skill.
