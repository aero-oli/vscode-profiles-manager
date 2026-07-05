#!/usr/bin/env python3
"""
Safe helper for creating, editing, auditing, backing up, and maintaining VS Code profiles.

Design goals:
- stdlib only
- readable output for agents
- no direct edits to VS Code internal state databases
- backups before mutation
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any, Iterable

# Editable defaults
DEFAULT_VARIANT = "code"  # code | insiders | codium | custom
DEFAULT_CODE_BIN_BY_VARIANT = {
    "code": "code",
    "insiders": "code-insiders",
    "codium": "codium",
    "custom": "code",
}
BACKUP_DIR_NAME = "vscode-profile-backups"
JSON_INDENT = 2


class VscodeProfileError(RuntimeError):
    pass


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def app_folder_for_variant(variant: str) -> str:
    mapping = {
        "code": "Code",
        "insiders": "Code - Insiders",
        "codium": "VSCodium",
        "custom": "Code",
    }
    return mapping.get(variant, "Code")


def user_dir_for_variant(variant: str, user_dir_override: str | None = None) -> Path:
    if user_dir_override:
        return expand_path(user_dir_override)

    app = app_folder_for_variant(variant)
    system = platform.system().lower()
    home = Path.home()

    if system == "darwin":
        return home / "Library" / "Application Support" / app / "User"
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise VscodeProfileError("APPDATA is not set; provide --user-dir explicitly.")
        return Path(appdata) / app / "User"
    # Linux and other Unix-like systems
    if variant == "codium":
        return home / ".config" / "VSCodium" / "User"
    return home / ".config" / app / "User"


def code_bin_for_variant(variant: str, code_bin: str | None = None) -> str:
    return code_bin or DEFAULT_CODE_BIN_BY_VARIANT.get(variant, "code")


def strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas from JSONC-like files.

    This is deliberately small and conservative. It handles normal VS Code JSONC settings well,
    but it is not a full JSONC parser.
    """
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1

    return strip_trailing_commas("".join(out))


def strip_trailing_commas(text: str) -> str:
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_jsonc(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return default if default is not None else {}
    try:
        return json.loads(strip_jsonc(text))
    except json.JSONDecodeError as exc:
        raise VscodeProfileError(f"Invalid JSON/JSONC in {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=JSON_INDENT, ensure_ascii=False) + "\n", encoding="utf-8")


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.{now_stamp()}.bak")
    shutil.copy2(path, backup)
    return backup


def merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = merge_dict(dict(base[key]), value)
        else:
            base[key] = value
    return base


def remove_keys(data: dict[str, Any], keys: Iterable[str]) -> None:
    for key in keys:
        data.pop(key, None)


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    eprint("+", " ".join(shlex_quote(x) for x in cmd))
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def shlex_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=@+~,-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def build_paths(args: argparse.Namespace) -> dict[str, str | bool]:
    user_dir = user_dir_for_variant(args.variant, args.user_dir)
    profiles_dir = user_dir / "profiles"
    return {
        "variant": args.variant,
        "user_dir": str(user_dir),
        "user_dir_exists": user_dir.exists(),
        "profiles_dir": str(profiles_dir),
        "profiles_dir_exists": profiles_dir.exists(),
        "default_settings": str(user_dir / "settings.json"),
        "default_keybindings": str(user_dir / "keybindings.json"),
        "default_tasks": str(user_dir / "tasks.json"),
        "default_snippets_dir": str(user_dir / "snippets"),
        "global_storage_dir": str(user_dir / "globalStorage"),
        "workspace_storage_dir": str(user_dir / "workspaceStorage"),
    }


def command_paths(args: argparse.Namespace) -> None:
    print(json.dumps(build_paths(args), indent=2))


def read_sync_profile_names(user_dir: Path) -> dict[str, str]:
    sync_path = user_dir / "sync" / "profiles" / "lastSyncprofiles.json"
    if not sync_path.exists():
        return {}
    try:
        raw = load_jsonc(sync_path)
        sync_data = raw.get("syncData", {}) if isinstance(raw, dict) else {}
        content = sync_data.get("content")
        profiles = json.loads(content) if isinstance(content, str) else content
    except Exception:
        return {}
    if not isinstance(profiles, list):
        return {}
    names: dict[str, str] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = profile.get("id")
        name = profile.get("name")
        if isinstance(profile_id, str) and isinstance(name, str):
            names[profile_id] = name
    return names


def discover_profiles(user_dir: Path) -> list[dict[str, Any]]:
    profiles_dir = user_dir / "profiles"
    names = read_sync_profile_names(user_dir)
    profile_ids: set[str] = set(names)
    if profiles_dir.exists():
        profile_ids.update(path.name for path in profiles_dir.iterdir() if path.is_dir() and not path.name.startswith("."))

    profiles: list[dict[str, Any]] = []
    for profile_id in sorted(profile_ids, key=lambda value: (names.get(value) or value).lower()):
        profile_dir = profiles_dir / profile_id
        source = "profiles-dir"
        if profile_id in names and profile_dir.exists():
            source = "profiles-dir+settings-sync"
        elif profile_id in names:
            source = "settings-sync"
        profiles.append(
            {
                "id": profile_id,
                "name": names.get(profile_id),
                "source": source,
                "profileDir": str(profile_dir),
                "profileDirExists": profile_dir.exists(),
                "settingsFile": str(profile_dir / "settings.json"),
                "keybindingsFile": str(profile_dir / "keybindings.json"),
                "tasksFile": str(profile_dir / "tasks.json"),
                "snippetsDir": str(profile_dir / "snippets"),
                "hasSettings": (profile_dir / "settings.json").exists(),
                "hasKeybindings": (profile_dir / "keybindings.json").exists(),
                "hasTasks": (profile_dir / "tasks.json").exists(),
                "hasSnippets": (profile_dir / "snippets").exists(),
            }
        )
    return profiles


def command_list_profiles(args: argparse.Namespace) -> None:
    user_dir = user_dir_for_variant(args.variant, args.user_dir)
    print(json.dumps({"userDir": str(user_dir), "profiles": discover_profiles(user_dir)}, indent=2))


def command_profile_setting_path(args: argparse.Namespace) -> None:
    user_dir = user_dir_for_variant(args.variant, args.user_dir)
    if not args.profile_id:
        raise VscodeProfileError("--profile-id is required")
    print(user_dir / "profiles" / args.profile_id / "settings.json")


def iter_backup_candidates(user_dir: Path, profile_id: str | None) -> list[Path]:
    if profile_id:
        candidates = [user_dir / "profiles" / profile_id]
    else:
        candidates = [
            user_dir / "settings.json",
            user_dir / "keybindings.json",
            user_dir / "tasks.json",
            user_dir / "snippets",
            user_dir / "profiles",
        ]
    return [p for p in candidates if p.exists()]


def zip_paths(paths: list[Path], out_dir: Path, label: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{label}-{now_stamp()}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            if path.is_file():
                zf.write(path, arcname=path.name)
            elif path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file():
                        zf.write(child, arcname=str(path.name / child.relative_to(path)))
    return zip_path


def command_backup(args: argparse.Namespace) -> None:
    user_dir = user_dir_for_variant(args.variant, args.user_dir)
    candidates = iter_backup_candidates(user_dir, args.profile_id)
    if not candidates:
        raise VscodeProfileError(f"Nothing found to back up under {user_dir}")
    out_dir = expand_path(args.out or (Path.home() / "Desktop" / BACKUP_DIR_NAME))
    label = f"{args.variant}-profile-{args.profile_id}" if args.profile_id else f"{args.variant}-user-config"
    zip_path = zip_paths(candidates, out_dir, label)
    print(json.dumps({"backup": str(zip_path), "items": [str(p) for p in candidates]}, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    path = expand_path(args.file)
    value = load_jsonc(path)
    print(json.dumps({"file": str(path), "valid": True, "type": type(value).__name__}, indent=2))


def command_merge_settings(args: argparse.Namespace) -> None:
    path = expand_path(args.file)
    updates: dict[str, Any] = {}
    if args.set_json:
        loaded = json.loads(args.set_json)
        if not isinstance(loaded, dict):
            raise VscodeProfileError("--set-json must be a JSON object")
        updates = loaded
    removals = args.remove_key or []

    current = load_jsonc(path, default={})
    if not isinstance(current, dict):
        raise VscodeProfileError(f"Expected top-level object in {path}, got {type(current).__name__}")

    backup = backup_file(path)
    updated = merge_dict(current, updates)
    remove_keys(updated, removals)
    write_json(path, updated)
    # Re-read to validate.
    load_jsonc(path)
    print(json.dumps({"file": str(path), "backup": str(backup) if backup else None, "set": list(updates), "removed": removals}, indent=2))


def command_list_extensions(args: argparse.Namespace) -> None:
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    cmd = [code_bin, "--list-extensions"]
    if args.show_versions:
        cmd.append("--show-versions")
    if args.profile:
        cmd += ["--profile", args.profile]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise VscodeProfileError(proc.stderr.strip() or f"Command failed: {' '.join(cmd)}")
    extensions = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    print(json.dumps({"profile": args.profile, "extensions": extensions}, indent=2))


def command_install_extensions(args: argparse.Namespace) -> None:
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    results: list[dict[str, Any]] = []
    for ext in args.extensions:
        cmd = [code_bin, "--install-extension", ext]
        if args.profile:
            cmd += ["--profile", args.profile]
        if args.force:
            cmd.append("--force")
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results.append({"extension": ext, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
        if proc.returncode != 0 and not args.continue_on_error:
            print(json.dumps({"results": results}, indent=2))
            raise SystemExit(proc.returncode)
    print(json.dumps({"profile": args.profile, "results": results}, indent=2))


def command_uninstall_extensions(args: argparse.Namespace) -> None:
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    results: list[dict[str, Any]] = []
    for ext in args.extensions:
        cmd = [code_bin, "--uninstall-extension", ext]
        if args.profile:
            cmd += ["--profile", args.profile]
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results.append({"extension": ext, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
        if proc.returncode != 0 and not args.continue_on_error:
            print(json.dumps({"results": results}, indent=2))
            raise SystemExit(proc.returncode)
    print(json.dumps({"profile": args.profile, "results": results}, indent=2))


def command_scaffold_spec(args: argparse.Namespace) -> None:
    spec = {
        "$schema": "./profile-spec.schema.json",
        "profile": args.profile,
        "variant": args.variant,
        "workspace": args.workspace or "~/tmp/vscode-profile-bootstrap",
        "extensions": [],
        "removeExtensions": [],
        "settings": {},
        "removeSettings": [],
        "notes": "Fill in the intended purpose, assumptions, and machine-specific exclusions. Add profileId or settingsFile before applying profile-file changes.",
    }
    if args.out:
        out = expand_path(args.out)
        write_json(out, spec)
        print(out)
    else:
        print(json.dumps(spec, indent=2))


def command_generate_commands(args: argparse.Namespace) -> None:
    spec_path = expand_path(args.spec)
    spec = load_jsonc(spec_path)
    profile = spec.get("profile")
    if not profile:
        raise VscodeProfileError("Spec must contain profile")
    variant = spec.get("variant", args.variant)
    code_bin = spec.get("codeBin") or code_bin_for_variant(variant, args.code_bin)
    workspace = spec.get("workspace") or "~/tmp/vscode-profile-bootstrap"
    extensions = spec.get("extensions", [])
    remove_extensions = spec.get("removeExtensions", [])

    lines = [
        f"mkdir -p {shlex_quote(workspace)} && {shlex_quote(code_bin)} {shlex_quote(workspace)} --profile {shlex_quote(profile)}"
    ]
    for ext in extensions:
        lines.append(f"{shlex_quote(code_bin)} --install-extension {shlex_quote(ext)} --profile {shlex_quote(profile)}")
    for ext in remove_extensions:
        lines.append(f"{shlex_quote(code_bin)} --uninstall-extension {shlex_quote(ext)} --profile {shlex_quote(profile)}")
    if spec_has_profile_file_changes(spec):
        user_dir = user_dir_for_variant(variant, args.user_dir)
        target = profile_dir_for_spec(spec, user_dir)
        if target:
            lines.append(f"# Profile file changes require backups; use apply-spec to write files under {shlex_quote(str(target))}.")
        else:
            lines.append("# Profile file changes require profileId or settingsFile before apply-spec can write them.")
    print("\n".join(lines))


def command_snapshot(args: argparse.Namespace) -> None:
    user_dir = user_dir_for_variant(args.variant, args.user_dir)
    snapshot: dict[str, Any] = {
        "createdAt": _dt.datetime.now().isoformat(timespec="seconds"),
        "variant": args.variant,
        "profile": args.profile,
        "userDir": str(user_dir),
    }
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    if args.profile:
        proc = subprocess.run([code_bin, "--list-extensions", "--show-versions", "--profile", args.profile], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        snapshot["extensionListReturnCode"] = proc.returncode
        snapshot["extensions"] = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if proc.stderr.strip():
            snapshot["extensionListStderr"] = proc.stderr.strip()
    if args.profile_id:
        settings_path = user_dir / "profiles" / args.profile_id / "settings.json"
        snapshot["profileId"] = args.profile_id
        snapshot["settingsFile"] = str(settings_path)
        snapshot["settings"] = load_jsonc(settings_path, default={}) if settings_path.exists() else {}
    default_settings = user_dir / "settings.json"
    if default_settings.exists() and args.include_default_settings:
        snapshot["defaultSettings"] = load_jsonc(default_settings, default={})
    if args.out:
        out = expand_path(args.out)
        write_json(out, snapshot)
        print(out)
    else:
        print(json.dumps(snapshot, indent=2))


def spec_has_profile_file_changes(spec: dict[str, Any]) -> bool:
    return bool(
        spec.get("settings")
        or spec.get("removeSettings")
        or spec.get("keybindings") is not None
        or spec.get("tasks") is not None
        or spec.get("snippets")
    )


def profile_dir_for_spec(spec: dict[str, Any], user_dir: Path) -> Path | None:
    settings_file = spec.get("settingsFile")
    if settings_file:
        return expand_path(settings_file).parent
    profile_id = spec.get("profileId")
    if profile_id:
        return user_dir / "profiles" / str(profile_id)
    return None


def validate_profile_file_spec(spec: dict[str, Any], require_target: bool, user_dir: Path) -> Path | None:
    if not spec_has_profile_file_changes(spec):
        return None
    if "settings" in spec and spec.get("settings") is not None and not isinstance(spec.get("settings"), dict):
        raise VscodeProfileError("settings must be a JSON object")
    if "removeSettings" in spec and not all(isinstance(key, str) for key in (spec.get("removeSettings") or [])):
        raise VscodeProfileError("removeSettings must be an array of strings")
    if "keybindings" in spec and spec.get("keybindings") is not None and not isinstance(spec.get("keybindings"), list):
        raise VscodeProfileError("keybindings must be a JSON array")
    if "tasks" in spec and spec.get("tasks") is not None and not isinstance(spec.get("tasks"), dict):
        raise VscodeProfileError("tasks must be a JSON object")
    snippets = spec.get("snippets") or {}
    if snippets and not isinstance(snippets, dict):
        raise VscodeProfileError("snippets must be an object keyed by snippet filename")
    for filename, value in snippets.items():
        path = Path(str(filename))
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise VscodeProfileError(f"Invalid snippet filename: {filename}")
        if not isinstance(value, dict):
            raise VscodeProfileError(f"Snippet file {filename} must contain a JSON object")

    profile_dir = profile_dir_for_spec(spec, user_dir)
    if require_target and profile_dir is None:
        raise VscodeProfileError("Profile file changes require settingsFile or profileId before any VS Code state is changed.")
    return profile_dir


def write_profile_data_files(spec: dict[str, Any], profile_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    settings = spec.get("settings") or {}
    remove_settings = spec.get("removeSettings") or []
    if settings or remove_settings:
        settings_file = expand_path(spec["settingsFile"]) if spec.get("settingsFile") else profile_dir / "settings.json"
        merge_args = argparse.Namespace(file=str(settings_file), set_json=json.dumps(settings), remove_key=remove_settings)
        command_merge_settings(merge_args)
        results.append({"file": str(settings_file), "kind": "settings"})
    if "keybindings" in spec and spec.get("keybindings") is not None:
        path = profile_dir / "keybindings.json"
        backup = backup_file(path)
        write_json(path, spec["keybindings"])
        load_jsonc(path)
        results.append({"file": str(path), "kind": "keybindings", "backup": str(backup) if backup else None})
    if "tasks" in spec and spec.get("tasks") is not None:
        path = profile_dir / "tasks.json"
        backup = backup_file(path)
        write_json(path, spec["tasks"])
        load_jsonc(path)
        results.append({"file": str(path), "kind": "tasks", "backup": str(backup) if backup else None})
    snippets = spec.get("snippets") or {}
    for filename, value in snippets.items():
        path = profile_dir / "snippets" / filename
        backup = backup_file(path)
        write_json(path, value)
        load_jsonc(path)
        results.append({"file": str(path), "kind": "snippet", "backup": str(backup) if backup else None})
    if results:
        print(json.dumps({"profileFiles": results}, indent=2))
    return results


def command_apply_spec(args: argparse.Namespace) -> None:
    spec_path = expand_path(args.spec)
    spec = load_jsonc(spec_path)
    if not isinstance(spec, dict):
        raise VscodeProfileError("Spec must be a JSON object")
    profile = spec.get("profile")
    if not profile:
        raise VscodeProfileError("Spec must contain profile")

    variant = spec.get("variant", args.variant)
    code_bin = spec.get("codeBin") or code_bin_for_variant(variant, args.code_bin)
    workspace = spec.get("workspace") or "~/tmp/vscode-profile-bootstrap"
    settings = spec.get("settings") or {}
    remove_settings = spec.get("removeSettings") or []
    extensions = spec.get("extensions") or []
    remove_extensions = spec.get("removeExtensions") or []
    user_dir = user_dir_for_variant(variant, args.user_dir)
    profile_dir = validate_profile_file_spec(spec, require_target=not args.dry_run, user_dir=user_dir)

    if args.dry_run:
        print("# Dry run commands")
        print(f"mkdir -p {shlex_quote(workspace)} && {shlex_quote(code_bin)} {shlex_quote(workspace)} --profile {shlex_quote(profile)}")
        for ext in extensions:
            print(f"{shlex_quote(code_bin)} --install-extension {shlex_quote(ext)} --profile {shlex_quote(profile)}")
        for ext in remove_extensions:
            print(f"{shlex_quote(code_bin)} --uninstall-extension {shlex_quote(ext)} --profile {shlex_quote(profile)}")
        if spec_has_profile_file_changes(spec):
            target = str(profile_dir) if profile_dir else "<settingsFile or profileId required>"
            print(f"# write profile data files under {target}")
        return

    # Create/open profile. This can open a GUI window; it is official and idempotent.
    workspace_path = expand_path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    subprocess.run([code_bin, str(workspace_path), "--profile", profile], check=True)

    install_args = argparse.Namespace(variant=variant, code_bin=code_bin, profile=profile, extensions=extensions, force=args.force, continue_on_error=args.continue_on_error)
    if extensions:
        command_install_extensions(install_args)
    uninstall_args = argparse.Namespace(variant=variant, code_bin=code_bin, profile=profile, extensions=remove_extensions, continue_on_error=args.continue_on_error)
    if remove_extensions:
        command_uninstall_extensions(uninstall_args)

    if profile_dir:
        write_profile_data_files(spec, profile_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safe helper for VS Code profile path discovery, backups, settings merges, snapshots, and extension management.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              python scripts/vscode_profile_manager.py paths --variant code
              python scripts/vscode_profile_manager.py backup --variant code --out ~/Desktop/vscode-profile-backups
              python scripts/vscode_profile_manager.py list-extensions --profile "Python" --show-versions
              python scripts/vscode_profile_manager.py merge-settings --file ~/settings.json --set-json '{"editor.formatOnSave":true}'
              python scripts/vscode_profile_manager.py generate-commands --spec assets/example-profile-spec.json
            """
        ),
    )
    parser.add_argument("--variant", default=DEFAULT_VARIANT, choices=["code", "insiders", "codium", "custom"], help="VS Code variant")
    parser.add_argument("--user-dir", help="Override VS Code User directory")
    parser.add_argument("--code-bin", help="Override VS Code CLI binary/path")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("paths", help="Show likely VS Code User/profile paths")
    p.set_defaults(func=command_paths)

    p = sub.add_parser("list-profiles", help="List known profile IDs, names, and profile file paths")
    p.set_defaults(func=command_list_profiles)

    p = sub.add_parser("profile-setting-path", help="Print profile settings.json path for a known profile ID")
    p.add_argument("--profile-id", required=True)
    p.set_defaults(func=command_profile_setting_path)

    p = sub.add_parser("backup", help="Create a timestamped zip backup of user/profile configuration")
    p.add_argument("--profile-id", help="Back up only this profile folder")
    p.add_argument("--out", help="Output directory")
    p.set_defaults(func=command_backup)

    p = sub.add_parser("validate", help="Validate a JSON/JSONC file")
    p.add_argument("--file", required=True)
    p.set_defaults(func=command_validate)

    p = sub.add_parser("merge-settings", help="Safely merge top-level settings into a settings.json file")
    p.add_argument("--file", required=True)
    p.add_argument("--set-json", help="JSON object of settings to merge")
    p.add_argument("--remove-key", action="append", help="Top-level setting key to remove; can be repeated")
    p.set_defaults(func=command_merge_settings)

    p = sub.add_parser("list-extensions", help="List extensions, optionally for a profile")
    p.add_argument("--profile")
    p.add_argument("--show-versions", action="store_true")
    p.set_defaults(func=command_list_extensions)

    p = sub.add_parser("install-extensions", help="Install extensions, optionally into a profile")
    p.add_argument("--profile")
    p.add_argument("--extensions", nargs="+", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--continue-on-error", action="store_true")
    p.set_defaults(func=command_install_extensions)

    p = sub.add_parser("uninstall-extensions", help="Uninstall extensions, optionally from a profile")
    p.add_argument("--profile")
    p.add_argument("--extensions", nargs="+", required=True)
    p.add_argument("--continue-on-error", action="store_true")
    p.set_defaults(func=command_uninstall_extensions)

    p = sub.add_parser("scaffold-spec", help="Create a profile manifest skeleton")
    p.add_argument("--profile", required=True)
    p.add_argument("--workspace")
    p.add_argument("--out")
    p.set_defaults(func=command_scaffold_spec)

    p = sub.add_parser("generate-commands", help="Generate shell commands from a profile manifest without executing them")
    p.add_argument("--spec", required=True)
    p.set_defaults(func=command_generate_commands)

    p = sub.add_parser("snapshot", help="Create a JSON snapshot of profile extensions and optionally settings")
    p.add_argument("--profile")
    p.add_argument("--profile-id")
    p.add_argument("--include-default-settings", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=command_snapshot)

    p = sub.add_parser("apply-spec", help="Apply a profile manifest; use --dry-run first")
    p.add_argument("--spec", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--continue-on-error", action="store_true")
    p.set_defaults(func=command_apply_spec)

    return parser


def normalize_global_args(argv: list[str]) -> list[str]:
    """Allow global options before or after the subcommand."""
    globals_with_values = {"--variant", "--user-dir", "--code-bin"}
    front: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        item = argv[i]
        if any(item.startswith(opt + "=") for opt in globals_with_values):
            front.append(item)
            i += 1
        elif item in globals_with_values and i + 1 < len(argv):
            front.extend([item, argv[i + 1]])
            i += 2
        else:
            rest.append(item)
            i += 1
    return front + rest


def main() -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_global_args(sys.argv[1:]))
    try:
        args.func(args)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI tool should print friendly errors
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
