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

Clear additions and updates proceed directly. Destructive removals, profile deletion, and restore require confirmation.

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

Backups include only supported configuration files and extension snapshots. They exclude VS Code databases, UI state, global/workspace storage, and extension state. Restore previews by default and does not reinstall or uninstall extensions automatically.

Install this directory under an agent skills directory such as `~/.agents/skills/vscode-profiles-manager`, or use it as a repository-local skill.
