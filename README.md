# VS Code Profiles Manager Skill

A Codex/agent skill for creating, editing, auditing, backing up, restoring, and maintaining Visual Studio Code profiles.

## Install

Copy the folder into your agent skills directory:

```bash
mkdir -p ~/.agents/skills && cp -R vscode-profiles-manager ~/.agents/skills/
```

If your agent host supports a skills CLI, install/import the folder with that CLI instead.

## What is included

```text
vscode-profiles-manager/
├── SKILL.md
├── README.md
├── agents/
│   └── openai.yaml
├── assets/
│   ├── example-profile-spec.json
│   └── profile-spec.schema.json
├── references/
│   └── vscode-profiles-research.md
└── scripts/
    └── vscode_profile_manager.py
```

## Typical commands

Show likely VS Code profile paths:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py paths --variant code
```

List known profile IDs and profile file paths:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py list-profiles --variant code
```

Back up stable VS Code profile/user config:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py backup --variant code --out ~/Desktop/vscode-profile-backups
```

Create a manifest skeleton:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py scaffold-spec --profile "Python" --out ~/Desktop/python-vscode-profile.json
```

Generate setup commands from a manifest:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py generate-commands --spec ~/Desktop/python-vscode-profile.json
```

Apply a manifest after adding `profileId` or `settingsFile` for profile-file changes:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py apply-spec --spec ~/Desktop/python-vscode-profile.json --dry-run
```

List extensions in a profile:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py list-extensions --profile "Python" --show-versions
```

Merge profile settings safely once you know the target `settings.json` path:

```bash
python ~/.agents/skills/vscode-profiles-manager/scripts/vscode_profile_manager.py merge-settings --file "/path/to/settings.json" --set-json '{"editor.formatOnSave":true}'
```

## Safety stance

The skill deliberately avoids direct edits to undocumented VS Code state stores unless explicitly requested. It uses official VS Code CLI/profile export behaviours where possible, and backs up JSON files before editing.
