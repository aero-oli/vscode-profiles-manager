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
import difflib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from dataclasses import dataclass
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
    """Return a collision-resistant, sortable local timestamp."""
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


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


def user_dir_for_variant(
    variant: str,
    user_dir_override: str | None = None,
    user_data_dir: str | None = None,
) -> Path:
    if user_dir_override:
        return expand_path(user_dir_override)
    if user_data_dir:
        return expand_path(user_data_dir) / "User"

    app = app_folder_for_variant(variant)
    system = platform.system().lower()
    home = Path.home()

    if system == "darwin":
        return home / "Library" / "Application Support" / app / "User"
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise VscodeProfileError(
                "APPDATA is not set; provide --user-dir explicitly."
            )
        return Path(appdata) / app / "User"
    # Linux and other Unix-like systems
    if variant == "codium":
        return home / ".config" / "VSCodium" / "User"
    return home / ".config" / app / "User"


def code_bin_for_variant(variant: str, code_bin: str | None = None) -> str:
    return code_bin or DEFAULT_CODE_BIN_BY_VARIANT.get(variant, "code")


def user_dir_from_args(args: argparse.Namespace, variant: str | None = None) -> Path:
    return user_dir_for_variant(
        variant or args.variant,
        getattr(args, "user_dir", None),
        getattr(args, "user_data_dir", None),
    )


def code_context_args(args: argparse.Namespace) -> list[str]:
    user_data_dir = getattr(args, "user_data_dir", None)
    return ["--user-data-dir", str(expand_path(user_data_dir))] if user_data_dir else []


def cli_context_issue(
    args: argparse.Namespace, variant: str | None = None
) -> str | None:
    """Explain when the VS Code CLI cannot target the helper's selected User directory."""
    if getattr(args, "user_data_dir", None) or not getattr(args, "user_dir", None):
        return None
    selected_variant = variant or args.variant
    selected_user_dir = expand_path(args.user_dir)
    default_user_dir = user_dir_for_variant(selected_variant).resolve()
    if selected_user_dir == default_user_dir:
        return None
    return (
        f"The VS Code CLI cannot target custom --user-dir {selected_user_dir}. "
        "Use --user-data-dir for CLI-scoped profile or extension operations."
    )


def require_cli_context(args: argparse.Namespace, variant: str | None = None) -> None:
    issue = cli_context_issue(args, variant)
    if issue:
        raise VscodeProfileError(issue)


def validate_profile_id(profile_id: str) -> str:
    if (
        not profile_id
        or profile_id in {".", ".."}
        or "/" in profile_id
        or "\\" in profile_id
        or "\x00" in profile_id
    ):
        raise VscodeProfileError(
            "profileId must be one non-empty profile-folder name, not a path"
        )
    return profile_id


def ensure_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise VscodeProfileError(
            f"{label} escapes the allowed directory {root_resolved}: {resolved}"
        ) from exc
    return resolved


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


def has_jsonc_comments(text: str) -> bool:
    """Return True when text contains line or block comments outside strings."""
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "/" and nxt in {"/", "*"}:
            return True
        i += 1
    return False


def atomic_write_bytes(path: Path, content: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = (path.stat().st_mode & 0o777) if path.exists() else None
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode if mode is not None else (existing_mode or 0o600))
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_json(path: Path, data: Any) -> None:
    content = (json.dumps(data, indent=JSON_INDENT, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    atomic_write_bytes(path, content)


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


def merge_settings_data(
    base: dict[str, Any], patch: dict[str, Any], strategy: str
) -> dict[str, Any]:
    if strategy == "deep":
        return merge_dict(dict(base), patch)
    if strategy != "replace":
        raise VscodeProfileError("settingsMerge must be 'replace' or 'deep'")
    updated = dict(base)
    updated.update(patch)
    return updated


def json_diff(path: Path, before: Any, after: Any) -> str:
    before_text = json.dumps(before, indent=JSON_INDENT, ensure_ascii=False).splitlines(
        keepends=True
    )
    after_text = json.dumps(after, indent=JSON_INDENT, ensure_ascii=False).splitlines(
        keepends=True
    )
    return "".join(
        difflib.unified_diff(
            before_text, after_text, fromfile=str(path), tofile=f"{path} (planned)"
        )
    )


def remove_keys(data: dict[str, Any], keys: Iterable[str]) -> None:
    for key in keys:
        data.pop(key, None)


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    eprint("+", " ".join(shlex_quote(x) for x in cmd))
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check
    )


def shlex_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=@+~,-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def build_paths(args: argparse.Namespace) -> dict[str, str | bool]:
    user_dir = user_dir_from_args(args)
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
        "default_mcp": str(user_dir / "mcp.json"),
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
        profile_ids.update(
            path.name
            for path in profiles_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    profiles: list[dict[str, Any]] = []
    for profile_id in sorted(
        profile_ids, key=lambda value: (names.get(value) or value).lower()
    ):
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
                "mcpFile": str(profile_dir / "mcp.json"),
                "snippetsDir": str(profile_dir / "snippets"),
                "hasSettings": (profile_dir / "settings.json").exists(),
                "hasKeybindings": (profile_dir / "keybindings.json").exists(),
                "hasTasks": (profile_dir / "tasks.json").exists(),
                "hasMcp": (profile_dir / "mcp.json").exists(),
                "hasSnippets": (profile_dir / "snippets").exists(),
            }
        )
    return profiles


def command_list_profiles(args: argparse.Namespace) -> None:
    user_dir = user_dir_from_args(args)
    print(
        json.dumps(
            {"userDir": str(user_dir), "profiles": discover_profiles(user_dir)},
            indent=2,
        )
    )


def command_profile_setting_path(args: argparse.Namespace) -> None:
    user_dir = user_dir_from_args(args)
    if not args.profile_id:
        raise VscodeProfileError("--profile-id is required")
    profile_id = validate_profile_id(args.profile_id)
    print(user_dir / "profiles" / profile_id / "settings.json")


@dataclass(frozen=True)
class BackupItem:
    source: Path
    archive_name: str


SAFE_PROFILE_FILES = ("settings.json", "keybindings.json", "tasks.json", "mcp.json")
SAFE_DEFAULT_FILES = SAFE_PROFILE_FILES
BACKUP_MANIFEST_NAME = "backup-manifest.json"
EXTENSIONS_SNAPSHOT_NAME = "extensions-snapshot.json"


def collect_snippet_items(snippets_dir: Path, archive_prefix: str) -> list[BackupItem]:
    items: list[BackupItem] = []
    if not snippets_dir.exists():
        return items
    for child in sorted(snippets_dir.rglob("*")):
        if child.is_symlink():
            continue
        if child.is_file():
            items.append(
                BackupItem(
                    child,
                    f"{archive_prefix}/{child.relative_to(snippets_dir).as_posix()}",
                )
            )
    return items


def collect_profile_backup_items(user_dir: Path, profile_id: str) -> list[BackupItem]:
    profile_id = validate_profile_id(profile_id)
    profile_dir = ensure_within(
        user_dir / "profiles" / profile_id, user_dir / "profiles", "profileId"
    )
    items = [
        BackupItem(profile_dir / filename, f"profiles/{profile_id}/{filename}")
        for filename in SAFE_PROFILE_FILES
        if (profile_dir / filename).is_file()
        and not (profile_dir / filename).is_symlink()
    ]
    items.extend(
        collect_snippet_items(
            profile_dir / "snippets", f"profiles/{profile_id}/snippets"
        )
    )
    return items


def collect_backup_items(user_dir: Path, profile_id: str | None) -> list[BackupItem]:
    if profile_id:
        return collect_profile_backup_items(user_dir, profile_id)
    items = [
        BackupItem(user_dir / filename, filename)
        for filename in SAFE_DEFAULT_FILES
        if (user_dir / filename).is_file() and not (user_dir / filename).is_symlink()
    ]
    items.extend(collect_snippet_items(user_dir / "snippets", "snippets"))
    profiles_dir = user_dir / "profiles"
    if profiles_dir.exists():
        for profile_dir in sorted(profiles_dir.iterdir()):
            if (
                profile_dir.is_dir()
                and not profile_dir.is_symlink()
                and not profile_dir.name.startswith(".")
            ):
                items.extend(collect_profile_backup_items(user_dir, profile_dir.name))
    return items


def extension_snapshot_for_profile(
    code_bin: str,
    profile: str | None,
    context_args: list[str],
) -> dict[str, Any]:
    cmd = [code_bin, "--list-extensions", "--show-versions", *context_args]
    if profile:
        cmd += ["--profile", profile]
    try:
        proc = subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        return {
            "profile": profile,
            "returncode": None,
            "extensions": [],
            "error": str(exc),
        }
    return {
        "profile": profile,
        "returncode": proc.returncode,
        "extensions": [
            line.strip() for line in proc.stdout.splitlines() if line.strip()
        ],
        "stderr": proc.stderr.strip() or None,
    }


def collect_extension_snapshots(
    args: argparse.Namespace,
    user_dir: Path,
    profile_id: str | None,
    profile_name: str | None,
) -> list[dict[str, Any]]:
    if getattr(args, "skip_extensions", False):
        return []
    context_issue = cli_context_issue(args)
    if context_issue:
        return [
            {
                "profile": profile_name,
                "returncode": None,
                "extensions": [],
                "error": f"Extension snapshot skipped: {context_issue}",
            }
        ]
    code_bin = code_bin_for_variant(args.variant, getattr(args, "code_bin", None))
    context = code_context_args(args)
    if profile_id:
        name = profile_name
        if not name:
            name = next(
                (
                    p["name"]
                    for p in discover_profiles(user_dir)
                    if p["id"] == profile_id
                ),
                None,
            )
        return [extension_snapshot_for_profile(code_bin, name, context)] if name else []
    if profile_name:
        return [extension_snapshot_for_profile(code_bin, profile_name, context)]
    snapshots = [extension_snapshot_for_profile(code_bin, None, context)]
    snapshots.extend(
        extension_snapshot_for_profile(code_bin, profile["name"], context)
        for profile in discover_profiles(user_dir)
        if profile.get("name") and profile.get("profileDirExists")
    )
    return snapshots


def create_backup_archive(
    args: argparse.Namespace,
    user_dir: Path,
    out_dir: Path,
    profile_id: str | None = None,
    profile_name: str | None = None,
    reason: str = "manual",
) -> tuple[Path, dict[str, Any]]:
    if profile_id:
        profile_id = validate_profile_id(profile_id)
    items = collect_backup_items(user_dir, profile_id)
    extension_snapshots = collect_extension_snapshots(
        args, user_dir, profile_id, profile_name
    )
    if not items and not extension_snapshots:
        raise VscodeProfileError(f"Nothing found to back up under {user_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    label = (
        f"{args.variant}-profile-{profile_id}"
        if profile_id
        else f"{args.variant}-user-config"
    )
    zip_path = out_dir / f"{label}-{now_stamp()}.zip"
    manifest = {
        "formatVersion": 1,
        "createdAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "variant": args.variant,
        "scope": "profile" if profile_id else "user-config",
        "profileId": profile_id,
        "profile": profile_name,
        "reason": reason,
        "userDir": str(user_dir),
        "files": [item.archive_name for item in items],
        "extensionsSnapshot": EXTENSIONS_SNAPSHOT_NAME if extension_snapshots else None,
        "restoreNote": "File restore does not reconcile installed extensions; use the snapshot as recovery evidence.",
    }
    if zip_path.exists():
        raise VscodeProfileError(f"Backup destination already exists: {zip_path}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{zip_path.name}.", suffix=".tmp", dir=out_dir
    )
    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "w+b") as archive_handle:
            with zipfile.ZipFile(
                archive_handle, "w", compression=zipfile.ZIP_DEFLATED
            ) as zf:
                for item in items:
                    zf.write(item.source, arcname=item.archive_name)
                zf.writestr(
                    BACKUP_MANIFEST_NAME,
                    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                )
                if extension_snapshots:
                    zf.writestr(
                        EXTENSIONS_SNAPSHOT_NAME,
                        json.dumps(extension_snapshots, indent=2, ensure_ascii=False)
                        + "\n",
                    )
            archive_handle.flush()
            os.fsync(archive_handle.fileno())
        os.replace(temp_path, zip_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return zip_path, manifest


def command_backup(args: argparse.Namespace) -> None:
    user_dir = user_dir_from_args(args)
    profile_id = validate_profile_id(args.profile_id) if args.profile_id else None
    if profile_id and not (user_dir / "profiles" / profile_id).is_dir():
        raise VscodeProfileError(
            f"Profile folder does not exist: {user_dir / 'profiles' / profile_id}"
        )
    if profile_id and args.profile:
        current_name = next(
            (
                item.get("name")
                for item in discover_profiles(user_dir)
                if item["id"] == profile_id
            ),
            None,
        )
        if current_name and current_name != args.profile:
            raise VscodeProfileError(
                f"Profile identity mismatch: ID {profile_id!r} is {current_name!r}, not {args.profile!r}"
            )
    out_dir = expand_path(args.out or (Path.home() / "Desktop" / BACKUP_DIR_NAME))
    zip_path, manifest = create_backup_archive(
        args,
        user_dir,
        out_dir,
        profile_id=profile_id,
        profile_name=args.profile,
    )
    print(json.dumps({"backup": str(zip_path), "manifest": manifest}, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    path = expand_path(args.file)
    value = load_jsonc(path)
    print(
        json.dumps(
            {"file": str(path), "valid": True, "type": type(value).__name__}, indent=2
        )
    )


def command_validate_spec(args: argparse.Namespace) -> None:
    path = expand_path(args.spec)
    spec = load_jsonc(path)
    warnings = validate_manifest(spec)
    variant = spec.get("variant", args.variant)
    user_dir = user_dir_from_args(args, variant)
    profile_dir = resolve_profile_target(
        spec,
        user_dir,
        allow_unverified=args.allow_unverified_profile,
        require_target=False,
    )
    if profile_dir is None and spec_has_profile_file_changes(spec):
        warnings.append(
            "Profile file changes need profileId or settingsFile before they can be planned"
        )
    print(
        json.dumps(
            {
                "file": str(path),
                "valid": True,
                "profile": spec["profile"],
                "profileDir": str(profile_dir) if profile_dir else None,
                "warnings": warnings,
            },
            indent=2,
        )
    )


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
        raise VscodeProfileError(
            f"Expected top-level object in {path}, got {type(current).__name__}"
        )

    original_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if (
        original_text
        and has_jsonc_comments(original_text)
        and not getattr(args, "allow_comment_loss", False)
    ):
        raise VscodeProfileError(
            f"{path} contains comments. Refusing a lossy rewrite; use VS Code, a targeted editor, or --allow-comment-loss."
        )
    strategy = getattr(args, "strategy", "replace")
    updated = merge_settings_data(current, updates, strategy)
    remove_keys(updated, removals)
    if getattr(args, "dry_run", False):
        print(json_diff(path, current, updated), end="")
        return
    backup = backup_file(path)
    write_json(path, updated)
    # Re-read to validate.
    load_jsonc(path)
    print(
        json.dumps(
            {
                "file": str(path),
                "backup": str(backup) if backup else None,
                "set": list(updates),
                "removed": removals,
                "strategy": strategy,
                "commentLossAllowed": bool(getattr(args, "allow_comment_loss", False)),
            },
            indent=2,
        )
    )


def command_list_extensions(args: argparse.Namespace) -> None:
    require_cli_context(args)
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    cmd = [code_bin, "--list-extensions", *code_context_args(args)]
    if args.show_versions:
        cmd.append("--show-versions")
    if args.profile:
        cmd += ["--profile", args.profile]
    proc = subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        raise VscodeProfileError(
            proc.stderr.strip() or f"Command failed: {' '.join(cmd)}"
        )
    extensions = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    print(json.dumps({"profile": args.profile, "extensions": extensions}, indent=2))


def command_install_extensions(args: argparse.Namespace) -> None:
    require_cli_context(args)
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    results: list[dict[str, Any]] = []
    for ext in args.extensions:
        cmd = [code_bin, "--install-extension", ext, *code_context_args(args)]
        if args.profile:
            cmd += ["--profile", args.profile]
        if args.force:
            cmd.append("--force")
        proc = subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        results.append(
            {
                "extension": ext,
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        )
        if proc.returncode != 0 and not args.continue_on_error:
            print(json.dumps({"results": results}, indent=2))
            raise SystemExit(proc.returncode)
    print(json.dumps({"profile": args.profile, "results": results}, indent=2))


def command_uninstall_extensions(args: argparse.Namespace) -> None:
    require_cli_context(args)
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    results: list[dict[str, Any]] = []
    for ext in args.extensions:
        cmd = [code_bin, "--uninstall-extension", ext, *code_context_args(args)]
        if args.profile:
            cmd += ["--profile", args.profile]
        proc = subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        results.append(
            {
                "extension": ext,
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        )
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
        "settingsMerge": "replace",
        "removeSettings": [],
        "mcpServers": {},
        "removeMcpServers": [],
        "notes": "Fill in the intended purpose, assumptions, and machine-specific exclusions. Add profileId or settingsFile before applying profile-file changes.",
    }
    if args.out:
        out = expand_path(args.out)
        schema_source = (
            Path(__file__).resolve().parents[1] / "assets" / "profile-spec.schema.json"
        )
        schema_target = out.parent / "profile-spec.schema.json"
        if not schema_target.exists():
            schema_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(schema_source, schema_target)
        write_json(out, spec)
        print(json.dumps({"spec": str(out), "schema": str(schema_target)}, indent=2))
    else:
        print(json.dumps(spec, indent=2))


def command_generate_commands(args: argparse.Namespace) -> None:
    spec_path = expand_path(args.spec)
    spec = load_jsonc(spec_path)
    validate_manifest(spec)
    profile = spec["profile"]
    variant = spec.get("variant", args.variant)
    require_cli_context(args, variant)
    code_bin = spec.get("codeBin") or code_bin_for_variant(variant, args.code_bin)
    workspace = expand_path(spec.get("workspace") or "~/tmp/vscode-profile-bootstrap")
    extensions = spec.get("extensions", [])
    remove_extensions = spec.get("removeExtensions", [])
    context = code_context_args(args)

    lines = [f"mkdir -p {shlex_quote(str(workspace))}"]
    open_cmd = [code_bin, str(workspace), *context, "--profile", profile]
    lines.append(" ".join(shlex_quote(part) for part in open_cmd))
    for ext in extensions:
        cmd = [code_bin, "--install-extension", ext, *context, "--profile", profile]
        lines.append(" ".join(shlex_quote(part) for part in cmd))
    for ext in remove_extensions:
        cmd = [code_bin, "--uninstall-extension", ext, *context, "--profile", profile]
        lines.append(" ".join(shlex_quote(part) for part in cmd))
    if spec_has_profile_file_changes(spec):
        user_dir = user_dir_from_args(args, variant)
        target = profile_dir_for_spec(spec, user_dir)
        if target:
            lines.append(
                f"# Use apply-spec to back up and atomically write files under {shlex_quote(str(target))}."
            )
        else:
            lines.append(
                "# Profile file changes require profileId or settingsFile before apply-spec can write them."
            )
    print("\n".join(lines))


def command_snapshot(args: argparse.Namespace) -> None:
    user_dir = user_dir_from_args(args)
    snapshot: dict[str, Any] = {
        "createdAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "variant": args.variant,
        "profile": args.profile,
        "userDir": str(user_dir),
    }
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    if args.profile:
        context_issue = cli_context_issue(args)
        extension_snapshot = (
            {
                "profile": args.profile,
                "returncode": None,
                "extensions": [],
                "error": f"Extension snapshot skipped: {context_issue}",
            }
            if context_issue
            else extension_snapshot_for_profile(
                code_bin, args.profile, code_context_args(args)
            )
        )
        snapshot["extensionSnapshot"] = extension_snapshot
    if args.profile_id:
        profile_id = validate_profile_id(args.profile_id)
        profile_dir = ensure_within(
            user_dir / "profiles" / profile_id, user_dir / "profiles", "profileId"
        )
        snapshot["profileId"] = profile_id
        snapshot["profileFiles"] = snapshot_profile_files(
            profile_dir, include_mcp=args.include_mcp
        )
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
        or spec.get("mcpServers")
        or spec.get("removeMcpServers")
    )


def profile_dir_for_spec(spec: dict[str, Any], user_dir: Path) -> Path | None:
    settings_file = spec.get("settingsFile")
    if settings_file:
        return expand_path(settings_file).parent
    profile_id = spec.get("profileId")
    if profile_id:
        return user_dir / "profiles" / validate_profile_id(str(profile_id))
    return None


MANIFEST_FIELDS = {
    "$schema",
    "profile",
    "variant",
    "codeBin",
    "workspace",
    "profileId",
    "settingsFile",
    "settings",
    "settingsMerge",
    "removeSettings",
    "extensions",
    "removeExtensions",
    "keybindings",
    "snippets",
    "tasks",
    "mcpServers",
    "removeMcpServers",
    "notes",
}


def validate_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise VscodeProfileError(f"{field} must be an array of non-empty strings")
    if len(set(value)) != len(value):
        raise VscodeProfileError(f"{field} must not contain duplicates")
    return value


MANIFEST_EXTENSION_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9][A-Za-z0-9.+_-]*)?$"
)


def validate_manifest_extensions(
    value: Any, field: str, allow_version: bool
) -> list[str]:
    extensions = validate_string_list(value, field)
    normalized_ids: list[str] = []
    for extension in extensions:
        if not MANIFEST_EXTENSION_RE.fullmatch(extension) or (
            not allow_version and "@" in extension
        ):
            qualifier = (
                " with an optional @version" if allow_version else " without @version"
            )
            raise VscodeProfileError(
                f"{field} entries must be Marketplace extension IDs{qualifier}; use the direct extension commands for VSIX files"
            )
        normalized_ids.append(extension.split("@", 1)[0].lower())
    if len(set(normalized_ids)) != len(normalized_ids):
        raise VscodeProfileError(
            f"{field} must not contain the same extension ID more than once"
        )
    return normalized_ids


def validate_manifest(spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        raise VscodeProfileError("Spec must be a JSON object")
    unknown = sorted(set(spec) - MANIFEST_FIELDS)
    if unknown:
        raise VscodeProfileError(f"Unknown manifest fields: {', '.join(unknown)}")
    profile = spec.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise VscodeProfileError("Spec must contain a non-empty profile string")
    variant = spec.get("variant", DEFAULT_VARIANT)
    if variant not in {"code", "insiders", "codium", "custom"}:
        raise VscodeProfileError("variant must be code, insiders, codium, or custom")
    for field in ("codeBin", "workspace", "settingsFile", "notes"):
        if (
            field in spec
            and spec[field] is not None
            and not isinstance(spec[field], str)
        ):
            raise VscodeProfileError(f"{field} must be a string")
    if spec.get("profileId") is not None:
        if not isinstance(spec["profileId"], str):
            raise VscodeProfileError("profileId must be a string")
        validate_profile_id(spec["profileId"])
    if (
        "settings" in spec
        and spec.get("settings") is not None
        and not isinstance(spec["settings"], dict)
    ):
        raise VscodeProfileError("settings must be a JSON object")
    if spec.get("settingsMerge", "replace") not in {"replace", "deep"}:
        raise VscodeProfileError("settingsMerge must be 'replace' or 'deep'")
    validate_string_list(spec.get("removeSettings"), "removeSettings")
    install_ids = validate_manifest_extensions(
        spec.get("extensions"), "extensions", allow_version=True
    )
    remove_extension_ids = validate_manifest_extensions(
        spec.get("removeExtensions"), "removeExtensions", allow_version=False
    )
    validate_string_list(spec.get("removeMcpServers"), "removeMcpServers")
    if "keybindings" in spec and spec.get("keybindings") is not None:
        if not isinstance(spec["keybindings"], list) or not all(
            isinstance(item, dict) for item in spec["keybindings"]
        ):
            raise VscodeProfileError("keybindings must be an array of JSON objects")
    if (
        "tasks" in spec
        and spec.get("tasks") is not None
        and not isinstance(spec["tasks"], dict)
    ):
        raise VscodeProfileError("tasks must be a JSON object")
    snippets = spec.get("snippets") or {}
    if not isinstance(snippets, dict):
        raise VscodeProfileError("snippets must be an object keyed by snippet filename")
    for filename, value in snippets.items():
        path = Path(str(filename))
        if (
            not isinstance(filename, str)
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) != 1
            or "/" in filename
            or "\\" in filename
        ):
            raise VscodeProfileError(f"Invalid snippet filename: {filename}")
        if not isinstance(value, dict):
            raise VscodeProfileError(
                f"Snippet file {filename} must contain a JSON object"
            )
    mcp_servers = spec.get("mcpServers") or {}
    if not isinstance(mcp_servers, dict) or not all(
        isinstance(name, str) and name.strip() and isinstance(config, dict)
        for name, config in mcp_servers.items()
    ):
        raise VscodeProfileError(
            "mcpServers must be an object of named server configuration objects"
        )
    overlap = set(install_ids) & set(remove_extension_ids)
    if overlap:
        raise VscodeProfileError(
            f"Extensions cannot be installed and removed together: {', '.join(sorted(overlap))}"
        )
    return potential_secret_warnings(mcp_servers)


def potential_secret_warnings(value: Any, path: str = "mcpServers") -> list[str]:
    warnings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if re.search(
                r"(token|secret|password|api.?key)", str(key), re.IGNORECASE
            ) and isinstance(child, str):
                if not re.search(r"\$\{(?:env|input):", child):
                    warnings.append(
                        f"Possible literal secret at {child_path}; prefer an env or input variable"
                    )
            warnings.extend(potential_secret_warnings(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            warnings.extend(potential_secret_warnings(child, f"{path}[{index}]"))
    return warnings


def resolve_profile_target(
    spec: dict[str, Any],
    user_dir: Path,
    allow_unverified: bool = False,
    require_target: bool = True,
) -> Path | None:
    if not spec_has_profile_file_changes(spec):
        return None
    profiles_root = (user_dir / "profiles").resolve()
    profile_id = spec.get("profileId")
    settings_file_value = spec.get("settingsFile")
    profile_dir: Path | None = None
    if profile_id:
        profile_id = validate_profile_id(profile_id)
        profile_dir = ensure_within(
            profiles_root / profile_id, profiles_root, "profileId"
        )
    if settings_file_value:
        settings_file = expand_path(settings_file_value)
        if settings_file.name != "settings.json":
            raise VscodeProfileError(
                "settingsFile must point to a file named settings.json"
            )
        settings_profile_dir = ensure_within(
            settings_file.parent, profiles_root, "settingsFile"
        )
        if settings_profile_dir.parent != profiles_root:
            raise VscodeProfileError(
                "settingsFile must be directly inside one named profile folder"
            )
        validate_profile_id(settings_profile_dir.name)
        if profile_dir and settings_profile_dir != profile_dir:
            raise VscodeProfileError(
                "profileId and settingsFile resolve to different profile folders"
            )
        profile_dir = settings_profile_dir
        profile_id = settings_profile_dir.name
    if profile_dir is None:
        if require_target:
            raise VscodeProfileError(
                "Profile file changes require settingsFile or profileId before any state is changed"
            )
        return None
    if not profile_dir.is_dir():
        raise VscodeProfileError(
            f"Profile folder does not exist: {profile_dir}. Create it with VS Code, then run list-profiles."
        )
    discovered = next(
        (item for item in discover_profiles(user_dir) if item["id"] == profile_id), None
    )
    discovered_name = discovered.get("name") if discovered else None
    if discovered_name and discovered_name != spec["profile"]:
        raise VscodeProfileError(
            f"Profile identity mismatch: ID {profile_id!r} is {discovered_name!r}, not {spec['profile']!r}"
        )
    if (
        profile_id
        and not discovered_name
        and not settings_file_value
        and not allow_unverified
    ):
        raise VscodeProfileError(
            "Could not verify profileId against a display name. Use settingsFile opened by VS Code or --allow-unverified-profile."
        )
    return profile_dir


def snapshot_profile_files(
    profile_dir: Path, include_mcp: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for filename in ("settings.json", "keybindings.json", "tasks.json"):
        path = profile_dir / filename
        if path.exists():
            result[filename] = load_jsonc(path)
    snippets_dir = profile_dir / "snippets"
    if snippets_dir.exists():
        result["snippets"] = {
            child.name: load_jsonc(child)
            for child in sorted(snippets_dir.iterdir())
            if child.is_file() and not child.is_symlink()
        }
    mcp_path = profile_dir / "mcp.json"
    if mcp_path.exists():
        result["mcp.json"] = (
            load_jsonc(mcp_path)
            if include_mcp
            else "omitted; pass --include-mcp to include potentially sensitive configuration"
        )
    return result


def ensure_rewrite_allowed(path: Path, allow_comment_loss: bool) -> None:
    if (
        path.exists()
        and has_jsonc_comments(path.read_text(encoding="utf-8"))
        and not allow_comment_loss
    ):
        raise VscodeProfileError(
            f"{path} contains comments. Use a targeted editor or rerun with --allow-comment-loss after reviewing the dry run."
        )


@dataclass
class PlannedFile:
    path: Path
    kind: str
    before: Any
    after: Any
    existed: bool


@dataclass
class FileState:
    path: Path
    existed: bool
    content: bytes | None
    mode: int | None


def plan_profile_files(
    spec: dict[str, Any], profile_dir: Path, allow_comment_loss: bool
) -> list[PlannedFile]:
    planned: list[PlannedFile] = []
    settings = spec.get("settings") or {}
    remove_settings = spec.get("removeSettings") or []
    if settings or remove_settings:
        path = (
            expand_path(spec["settingsFile"])
            if spec.get("settingsFile")
            else profile_dir / "settings.json"
        )
        before = load_jsonc(path, default={})
        if not isinstance(before, dict):
            raise VscodeProfileError(f"Expected top-level object in {path}")
        ensure_rewrite_allowed(path, allow_comment_loss)
        after = merge_settings_data(
            before, settings, spec.get("settingsMerge", "replace")
        )
        remove_keys(after, remove_settings)
        planned.append(PlannedFile(path, "settings", before, after, path.exists()))
    for field, filename in (
        ("keybindings", "keybindings.json"),
        ("tasks", "tasks.json"),
    ):
        if field in spec and spec[field] is not None:
            path = profile_dir / filename
            ensure_rewrite_allowed(path, allow_comment_loss)
            before = load_jsonc(path, default=[] if field == "keybindings" else {})
            planned.append(PlannedFile(path, field, before, spec[field], path.exists()))
    for filename, value in (spec.get("snippets") or {}).items():
        path = ensure_within(
            profile_dir / "snippets" / filename,
            profile_dir / "snippets",
            "snippet filename",
        )
        ensure_rewrite_allowed(path, allow_comment_loss)
        before = load_jsonc(path, default={})
        planned.append(PlannedFile(path, "snippet", before, value, path.exists()))
    mcp_updates = spec.get("mcpServers") or {}
    mcp_removals = spec.get("removeMcpServers") or []
    if mcp_updates or mcp_removals:
        path = profile_dir / "mcp.json"
        ensure_rewrite_allowed(path, allow_comment_loss)
        before = load_jsonc(path, default={})
        if not isinstance(before, dict):
            raise VscodeProfileError(f"Expected top-level object in {path}")
        after = dict(before)
        servers = after.get("servers", {})
        if not isinstance(servers, dict):
            raise VscodeProfileError(f"Expected servers object in {path}")
        servers = dict(servers)
        servers.update(mcp_updates)
        remove_keys(servers, mcp_removals)
        after["servers"] = servers
        planned.append(PlannedFile(path, "mcp", before, after, path.exists()))
    return planned


def capture_file_states(planned: list[PlannedFile]) -> list[FileState]:
    return [
        FileState(
            item.path,
            item.path.exists(),
            item.path.read_bytes() if item.path.exists() else None,
            (item.path.stat().st_mode & 0o777) if item.path.exists() else None,
        )
        for item in planned
    ]


def restore_file_states(states: list[FileState]) -> list[str]:
    errors: list[str] = []
    for state in reversed(states):
        try:
            if state.existed and state.content is not None:
                atomic_write_bytes(state.path, state.content, state.mode)
            elif state.path.exists():
                state.path.unlink()
        except OSError as exc:
            errors.append(f"{state.path}: {exc}")
    return errors


def extension_id_and_version(value: str) -> tuple[str, str | None]:
    if value.lower().endswith(".vsix") or "/" in value or "\\" in value:
        return value, None
    if "@" in value:
        extension_id, version = value.rsplit("@", 1)
        return extension_id.lower(), version
    return value.lower(), None


def extension_map(lines: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        extension_id, version = extension_id_and_version(line.strip())
        result[extension_id] = f"{extension_id}@{version}" if version else extension_id
    return result


def run_extension_change(
    code_bin: str,
    profile: str,
    extension: str,
    install: bool,
    context: list[str],
    force: bool = False,
) -> dict[str, Any]:
    flag = "--install-extension" if install else "--uninstall-extension"
    cmd = [code_bin, flag, extension, *context, "--profile", profile]
    if install and force:
        cmd.append("--force")
    proc = subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    result = {
        "action": "install" if install else "uninstall",
        "extension": extension,
        "returncode": proc.returncode,
    }
    if proc.returncode != 0:
        raise VscodeProfileError(
            proc.stderr.strip() or f"Command failed: {' '.join(cmd)}"
        )
    return result


def rollback_touched_extensions(
    code_bin: str,
    profile: str,
    context: list[str],
    initial: dict[str, str],
    touched: set[str],
) -> list[str]:
    errors: list[str] = []
    current_snapshot = extension_snapshot_for_profile(code_bin, profile, context)
    current = extension_map(current_snapshot.get("extensions", []))
    for extension_id in sorted(touched):
        try:
            if extension_id in initial:
                if current.get(extension_id) != initial[extension_id]:
                    run_extension_change(
                        code_bin,
                        profile,
                        initial[extension_id],
                        True,
                        context,
                        force=True,
                    )
            elif extension_id in current:
                run_extension_change(code_bin, profile, extension_id, False, context)
        except Exception as exc:  # noqa: BLE001 - best-effort rollback reports every failure
            errors.append(f"{extension_id}: {exc}")
    return errors


def apply_plan_summary(
    spec: dict[str, Any],
    profile_dir: Path | None,
    planned: list[PlannedFile],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "profile": spec["profile"],
        "profileId": profile_dir.name if profile_dir else None,
        "profileDir": str(profile_dir) if profile_dir else None,
        "files": [
            {
                "path": str(item.path),
                "kind": item.kind,
                "operation": "replace" if item.existed else "create",
                "diff": json_diff(item.path, item.before, item.after),
            }
            for item in planned
        ],
        "installExtensions": spec.get("extensions") or [],
        "removeExtensions": spec.get("removeExtensions") or [],
        "warnings": warnings,
    }


def command_apply_spec(args: argparse.Namespace) -> None:
    spec_path = expand_path(args.spec)
    spec = load_jsonc(spec_path)
    warnings = validate_manifest(spec)
    variant = spec.get("variant", args.variant)
    user_dir = user_dir_from_args(args, variant)
    profile_dir = resolve_profile_target(
        spec,
        user_dir,
        allow_unverified=args.allow_unverified_profile,
        require_target=not args.dry_run,
    )
    if profile_dir is None and spec_has_profile_file_changes(spec):
        warnings.append(
            "Profile file changes need profileId or settingsFile before they can be planned"
        )
    planned = (
        plan_profile_files(spec, profile_dir, args.allow_comment_loss)
        if profile_dir
        else []
    )
    destructive = bool(
        spec.get("removeExtensions")
        or spec.get("removeSettings")
        or spec.get("removeMcpServers")
    )
    if destructive and not args.confirm_destructive and not args.dry_run:
        raise VscodeProfileError(
            "Removal fields require --confirm-destructive after reviewing --dry-run"
        )
    summary = apply_plan_summary(spec, profile_dir, planned, warnings)
    if args.dry_run:
        print(json.dumps({"dryRun": True, **summary}, indent=2))
        return

    code_bin = spec.get("codeBin") or code_bin_for_variant(variant, args.code_bin)
    context = code_context_args(args)
    has_extension_changes = bool(spec.get("extensions") or spec.get("removeExtensions"))
    if has_extension_changes:
        require_cli_context(args, variant)
    initial_snapshot = (
        extension_snapshot_for_profile(code_bin, spec["profile"], context)
        if has_extension_changes
        else None
    )
    if initial_snapshot and initial_snapshot.get("returncode") != 0:
        raise VscodeProfileError(
            initial_snapshot.get("stderr")
            or initial_snapshot.get("error")
            or "Could not snapshot profile extensions"
        )
    initial_extensions = (
        extension_map(initial_snapshot.get("extensions", []))
        if initial_snapshot
        else {}
    )
    touched = {
        extension_id_and_version(value)[0]
        for value in [
            *(spec.get("extensions") or []),
            *(spec.get("removeExtensions") or []),
        ]
    }
    backup_args = argparse.Namespace(**vars(args))
    backup_args.variant = variant
    backup_args.code_bin = code_bin
    backup_args.skip_extensions = False
    backup_dir = expand_path(
        args.backup_dir or (Path.home() / "Desktop" / BACKUP_DIR_NAME)
    )
    backup_profile_id = (
        profile_dir.name
        if profile_dir
        else next(
            (
                item["id"]
                for item in discover_profiles(user_dir)
                if item.get("name") == spec["profile"] and item.get("profileDirExists")
            ),
            None,
        )
    )
    backup_path, _ = create_backup_archive(
        backup_args,
        user_dir,
        backup_dir,
        profile_id=backup_profile_id,
        profile_name=spec["profile"],
        reason="pre-apply-spec",
    )
    states = capture_file_states(planned)
    changes: list[dict[str, Any]] = []
    try:
        for item in planned:
            write_json(item.path, item.after)
            load_jsonc(item.path)
            changes.append(
                {"action": "write", "kind": item.kind, "path": str(item.path)}
            )
        for extension in spec.get("extensions") or []:
            changes.append(
                run_extension_change(
                    code_bin, spec["profile"], extension, True, context, args.force
                )
            )
        for extension in spec.get("removeExtensions") or []:
            changes.append(
                run_extension_change(
                    code_bin, spec["profile"], extension, False, context
                )
            )
    except Exception as exc:
        file_errors = restore_file_states(states)
        extension_errors = (
            rollback_touched_extensions(
                code_bin, spec["profile"], context, initial_extensions, touched
            )
            if touched
            else []
        )
        detail = {
            "error": str(exc),
            "recoveryArchive": str(backup_path),
            "fileRollbackErrors": file_errors,
            "extensionRollbackErrors": extension_errors,
        }
        raise VscodeProfileError(
            f"Apply failed; rollback attempted: {json.dumps(detail)}"
        ) from exc
    print(
        json.dumps(
            {
                "applied": True,
                **summary,
                "changes": changes,
                "recoveryArchive": str(backup_path),
            },
            indent=2,
        )
    )


def command_open_profile(args: argparse.Namespace) -> None:
    require_cli_context(args)
    workspace = expand_path(args.workspace)
    cmd = [
        code_bin_for_variant(args.variant, args.code_bin),
        str(workspace),
        *code_context_args(args),
        "--profile",
        args.profile,
    ]
    if args.dry_run:
        print(
            json.dumps(
                {"dryRun": True, "workspace": str(workspace), "command": cmd}, indent=2
            )
        )
        return
    workspace.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        raise VscodeProfileError(
            proc.stderr.strip() or f"Command failed: {' '.join(cmd)}"
        )
    print(
        json.dumps(
            {"opened": True, "profile": args.profile, "workspace": str(workspace)},
            indent=2,
        )
    )


def command_doctor(args: argparse.Namespace) -> None:
    code_bin = code_bin_for_variant(args.variant, args.code_bin)
    resolved_bin = (
        shutil.which(code_bin) if not Path(code_bin).is_absolute() else code_bin
    )
    user_dir = user_dir_from_args(args)
    report: dict[str, Any] = {
        "ok": bool(resolved_bin),
        "platform": platform.system(),
        "python": platform.python_version(),
        "variant": args.variant,
        "codeBin": code_bin,
        "resolvedCodeBin": resolved_bin,
        "userDir": str(user_dir),
        "userDirExists": user_dir.exists(),
        "profileCount": len(discover_profiles(user_dir)),
        "userDataDir": str(expand_path(args.user_data_dir))
        if args.user_data_dir
        else None,
        "cliContextWarning": cli_context_issue(args),
    }
    if resolved_bin:
        proc = subprocess.run(
            [str(resolved_bin), "--version", *code_context_args(args)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        report["cliReturnCode"] = proc.returncode
        report["cliVersion"] = (
            proc.stdout.splitlines()[0] if proc.stdout.splitlines() else None
        )
        report["cliStderr"] = proc.stderr.strip() or None
        report["ok"] = report["ok"] and proc.returncode == 0
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


def read_backup_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    manifest_infos = [
        info for info in zf.infolist() if info.filename == BACKUP_MANIFEST_NAME
    ]
    if len(manifest_infos) != 1:
        raise VscodeProfileError(
            "Archive must contain exactly one backup-manifest.json"
        )
    if manifest_infos[0].file_size > 1024 * 1024:
        raise VscodeProfileError("Backup manifest exceeds the 1 MiB safe limit")
    try:
        manifest = json.loads(zf.read(manifest_infos[0]))
    except json.JSONDecodeError as exc:
        raise VscodeProfileError(f"Invalid backup manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("formatVersion") != 1:
        raise VscodeProfileError("Unsupported backup archive format")
    return manifest


def validate_restore_member_name(name: str, scope: str, profile_id: str | None) -> None:
    if (
        name in {BACKUP_MANIFEST_NAME, EXTENSIONS_SNAPSHOT_NAME}
        or name.startswith("/")
        or "\\" in name
    ):
        raise VscodeProfileError(f"Unsafe archive member: {name}")
    parts = name.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise VscodeProfileError(f"Unsafe archive member: {name}")

    def valid_profile_tail(tail: list[str]) -> bool:
        return (len(tail) == 1 and tail[0] in SAFE_PROFILE_FILES) or (
            len(tail) >= 2 and tail[0] == "snippets"
        )

    if scope == "profile":
        if (
            len(parts) < 3
            or parts[0] != "profiles"
            or parts[1] != profile_id
            or not valid_profile_tail(parts[2:])
        ):
            raise VscodeProfileError(f"Unexpected profile backup member: {name}")
        return
    if scope == "user-config":
        if len(parts) == 1 and parts[0] in SAFE_DEFAULT_FILES:
            return
        if len(parts) >= 2 and parts[0] == "snippets":
            return
        if len(parts) >= 3 and parts[0] == "profiles":
            validate_profile_id(parts[1])
            if valid_profile_tail(parts[2:]):
                return
        raise VscodeProfileError(f"Unexpected user-config backup member: {name}")
    raise VscodeProfileError(f"Unsupported backup scope: {scope}")


def restore_members(
    zf: zipfile.ZipFile, manifest: dict[str, Any], user_dir: Path
) -> list[tuple[zipfile.ZipInfo, Path]]:
    declared = manifest.get("files")
    if not isinstance(declared, list) or not all(
        isinstance(name, str) for name in declared
    ):
        raise VscodeProfileError("Backup manifest has an invalid files list")
    scope = manifest.get("scope")
    profile_id = manifest.get("profileId")
    if scope == "profile":
        profile_id = validate_profile_id(profile_id)
    elif scope != "user-config":
        raise VscodeProfileError(f"Unsupported backup scope: {scope}")
    if len(set(declared)) != len(declared):
        raise VscodeProfileError("Backup manifest contains duplicate file entries")
    archive_names = [info.filename for info in zf.infolist()]
    if len(set(archive_names)) != len(archive_names):
        raise VscodeProfileError("Backup archive contains duplicate member names")
    members: list[tuple[zipfile.ZipInfo, Path]] = []
    targets: set[Path] = set()
    total_size = 0
    for name in declared:
        validate_restore_member_name(name, scope, profile_id)
        try:
            info = zf.getinfo(name)
        except KeyError as exc:
            raise VscodeProfileError(
                f"Backup archive is missing declared member: {name}"
            ) from exc
        if info.is_dir():
            raise VscodeProfileError(f"Directory members are not allowed: {name}")
        if info.file_size > 20 * 1024 * 1024:
            raise VscodeProfileError(f"Archive member is unexpectedly large: {name}")
        total_size += info.file_size
        if total_size > 100 * 1024 * 1024:
            raise VscodeProfileError(
                "Backup archive exceeds the 100 MiB safe restore limit"
            )
        member_mode = (info.external_attr >> 16) & 0o170000
        if member_mode == stat.S_IFLNK:
            raise VscodeProfileError(f"Symlink members are not allowed: {name}")
        target = ensure_within(user_dir / Path(name), user_dir, "archive member")
        if target in targets:
            raise VscodeProfileError(
                f"Multiple archive members resolve to the same target: {target}"
            )
        targets.add(target)
        members.append((info, target))
    return members


def command_restore(args: argparse.Namespace) -> None:
    archive = expand_path(args.archive)
    if not archive.is_file():
        raise VscodeProfileError(f"Backup archive does not exist: {archive}")
    user_dir = user_dir_from_args(args)
    with zipfile.ZipFile(archive, "r") as zf:
        manifest = read_backup_manifest(zf)
        if manifest.get("variant") != args.variant and not args.allow_variant_mismatch:
            raise VscodeProfileError(
                f"Archive variant is {manifest.get('variant')!r}, but target variant is {args.variant!r}; use the matching --variant or --allow-variant-mismatch"
            )
        if (
            manifest.get("scope") == "profile"
            and manifest.get("profile")
            and not args.allow_profile_mismatch
        ):
            current = next(
                (
                    item
                    for item in discover_profiles(user_dir)
                    if item["id"] == manifest.get("profileId")
                ),
                None,
            )
            if (
                current
                and current.get("name")
                and current["name"] != manifest["profile"]
            ):
                raise VscodeProfileError(
                    f"Profile identity mismatch: archive is {manifest['profile']!r}, current ID is {current['name']!r}"
                )
        members = restore_members(zf, manifest, user_dir)
        plan = {
            "archive": str(archive),
            "targetUserDir": str(user_dir),
            "scope": manifest.get("scope"),
            "profileId": manifest.get("profileId"),
            "files": [str(target) for _, target in members],
            "extensionsRestored": False,
            "warning": "Close VS Code before confirming a multi-file restore. Extensions are not reconciled automatically.",
        }
        if not args.confirm:
            print(json.dumps({"dryRun": True, **plan}, indent=2))
            return
        recovery_path: Path | None = None
        backup_args = argparse.Namespace(**vars(args))
        backup_args.profile = manifest.get("profile")
        backup_args.profile_id = manifest.get("profileId")
        backup_args.skip_extensions = False
        try:
            recovery_path, _ = create_backup_archive(
                backup_args,
                user_dir,
                expand_path(
                    args.backup_dir or (Path.home() / "Desktop" / BACKUP_DIR_NAME)
                ),
                profile_id=manifest.get("profileId")
                if manifest.get("scope") == "profile"
                else None,
                profile_name=manifest.get("profile"),
                reason="pre-restore",
            )
        except VscodeProfileError:
            recovery_path = None
        states = [
            FileState(
                target,
                target.exists(),
                target.read_bytes() if target.exists() else None,
                (target.stat().st_mode & 0o777) if target.exists() else None,
            )
            for _, target in members
        ]
        try:
            for info, target in members:
                atomic_write_bytes(target, zf.read(info), 0o600)
        except Exception as exc:
            rollback_errors = restore_file_states(states)
            raise VscodeProfileError(
                f"Restore failed; rollback attempted: {exc}; rollback errors: {rollback_errors}"
            ) from exc
    print(
        json.dumps(
            {
                "restored": True,
                **plan,
                "recoveryArchive": str(recovery_path) if recovery_path else None,
            },
            indent=2,
        )
    )


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
    parser.add_argument(
        "--variant",
        default=DEFAULT_VARIANT,
        choices=["code", "insiders", "codium", "custom"],
        help="VS Code variant",
    )
    parser.add_argument("--user-dir", help="Override the exact VS Code User directory")
    parser.add_argument(
        "--user-data-dir",
        help="Use an isolated VS Code --user-data-dir; its User child stores profile files",
    )
    parser.add_argument("--code-bin", help="Override VS Code CLI binary/path")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("paths", help="Show likely VS Code User/profile paths")
    p.set_defaults(func=command_paths)

    p = sub.add_parser(
        "list-profiles", help="List known profile IDs, names, and profile file paths"
    )
    p.set_defaults(func=command_list_profiles)

    p = sub.add_parser(
        "profile-setting-path",
        help="Print profile settings.json path for a known profile ID",
    )
    p.add_argument("--profile-id", required=True)
    p.set_defaults(func=command_profile_setting_path)

    p = sub.add_parser(
        "doctor",
        help="Check Python, VS Code CLI, paths, and profile discovery without changing state",
    )
    p.set_defaults(func=command_doctor)

    p = sub.add_parser(
        "open-profile",
        help="Create/open a profile for a workspace through the official CLI",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_open_profile)

    p = sub.add_parser(
        "backup", help="Create a timestamped zip backup of user/profile configuration"
    )
    p.add_argument("--profile-id", help="Back up only this profile folder")
    p.add_argument(
        "--profile", help="Profile display name used for its extension snapshot"
    )
    p.add_argument(
        "--skip-extensions",
        action="store_true",
        help="Do not call the VS Code CLI to snapshot extensions",
    )
    p.add_argument("--out", help="Output directory")
    p.set_defaults(func=command_backup)

    p = sub.add_parser(
        "restore",
        help="Preview or restore safe profile files from a helper-created backup",
    )
    p.add_argument("--archive", required=True)
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Apply the restore; otherwise only print the plan",
    )
    p.add_argument(
        "--backup-dir", help="Directory for the automatic pre-restore recovery archive"
    )
    p.add_argument("--allow-variant-mismatch", action="store_true")
    p.add_argument("--allow-profile-mismatch", action="store_true")
    p.set_defaults(func=command_restore)

    p = sub.add_parser("validate", help="Validate a JSON/JSONC file")
    p.add_argument("--file", required=True)
    p.set_defaults(func=command_validate)

    p = sub.add_parser(
        "validate-spec",
        help="Validate a profile manifest and its target without changing state",
    )
    p.add_argument("--spec", required=True)
    p.add_argument("--allow-unverified-profile", action="store_true")
    p.set_defaults(func=command_validate_spec)

    p = sub.add_parser(
        "merge-settings",
        help="Safely merge top-level settings into a settings.json file",
    )
    p.add_argument("--file", required=True)
    p.add_argument("--set-json", help="JSON object of settings to merge")
    p.add_argument(
        "--remove-key",
        action="append",
        help="Top-level setting key to remove; can be repeated",
    )
    p.add_argument(
        "--strategy",
        choices=["replace", "deep"],
        default="replace",
        help="Replace object-valued settings or deep-merge them",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Print a diff without writing"
    )
    p.add_argument(
        "--allow-comment-loss",
        action="store_true",
        help="Allow rewriting a commented JSONC file as plain JSON",
    )
    p.set_defaults(func=command_merge_settings)

    p = sub.add_parser(
        "list-extensions", help="List extensions, optionally for a profile"
    )
    p.add_argument("--profile")
    p.add_argument("--show-versions", action="store_true")
    p.set_defaults(func=command_list_extensions)

    p = sub.add_parser(
        "install-extensions", help="Install extensions, optionally into a profile"
    )
    p.add_argument("--profile")
    p.add_argument("--extensions", nargs="+", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--continue-on-error", action="store_true")
    p.set_defaults(func=command_install_extensions)

    p = sub.add_parser(
        "uninstall-extensions", help="Uninstall extensions, optionally from a profile"
    )
    p.add_argument("--profile")
    p.add_argument("--extensions", nargs="+", required=True)
    p.add_argument("--continue-on-error", action="store_true")
    p.set_defaults(func=command_uninstall_extensions)

    p = sub.add_parser("scaffold-spec", help="Create a profile manifest skeleton")
    p.add_argument("--profile", required=True)
    p.add_argument("--workspace")
    p.add_argument("--out")
    p.set_defaults(func=command_scaffold_spec)

    p = sub.add_parser(
        "generate-commands",
        help="Generate shell commands from a profile manifest without executing them",
    )
    p.add_argument("--spec", required=True)
    p.set_defaults(func=command_generate_commands)

    p = sub.add_parser(
        "snapshot",
        help="Create a JSON snapshot of profile extensions and optionally settings",
    )
    p.add_argument("--profile")
    p.add_argument("--profile-id")
    p.add_argument("--include-default-settings", action="store_true")
    p.add_argument(
        "--include-mcp",
        action="store_true",
        help="Include potentially sensitive mcp.json content",
    )
    p.add_argument("--out")
    p.set_defaults(func=command_snapshot)

    p = sub.add_parser(
        "apply-spec", help="Apply a profile manifest; use --dry-run first"
    )
    p.add_argument("--spec", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--confirm-destructive",
        action="store_true",
        help="Confirm removal fields after reviewing --dry-run",
    )
    p.add_argument(
        "--allow-unverified-profile",
        action="store_true",
        help="Allow a profileId whose display name cannot be verified",
    )
    p.add_argument(
        "--allow-comment-loss",
        action="store_true",
        help="Allow rewriting commented settings JSONC as plain JSON",
    )
    p.add_argument(
        "--backup-dir", help="Directory for the automatic pre-apply recovery archive"
    )
    p.set_defaults(func=command_apply_spec)

    return parser


def normalize_global_args(argv: list[str]) -> list[str]:
    """Allow global options before or after the subcommand."""
    globals_with_values = {"--variant", "--user-dir", "--user-data-dir", "--code-bin"}
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
