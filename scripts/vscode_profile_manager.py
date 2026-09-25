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


def default_profile_info(user_dir: Path) -> dict[str, Any]:
    return {
        "id": None,
        "name": "Default",
        "source": "default",
        "profileDir": str(user_dir),
        "profileDirExists": user_dir.exists(),
        "settingsFile": str(user_dir / "settings.json"),
        "keybindingsFile": str(user_dir / "keybindings.json"),
        "tasksFile": str(user_dir / "tasks.json"),
        "mcpFile": str(user_dir / "mcp.json"),
        "snippetsDir": str(user_dir / "snippets"),
        "hasSettings": (user_dir / "settings.json").exists(),
        "hasKeybindings": (user_dir / "keybindings.json").exists(),
        "hasTasks": (user_dir / "tasks.json").exists(),
        "hasMcp": (user_dir / "mcp.json").exists(),
        "hasSnippets": (user_dir / "snippets").exists(),
    }


def resolve_profile_reference(
    user_dir: Path, reference: str
) -> tuple[dict[str, Any], str]:
    """Resolve a profile display name first, with an exact ID as fallback."""
    if not reference.strip():
        raise VscodeProfileError("Profile name must not be empty")
    if reference.casefold() == "default":
        return default_profile_info(user_dir), "default"

    profiles = discover_profiles(user_dir)
    exact_names = [item for item in profiles if item.get("name") == reference]
    if len(exact_names) == 1:
        return exact_names[0], "name"
    if len(exact_names) > 1:
        raise VscodeProfileError(f"Profile name is ambiguous: {reference!r}")

    folded_names = [
        item
        for item in profiles
        if isinstance(item.get("name"), str)
        and item["name"].casefold() == reference.casefold()
    ]
    if len(folded_names) == 1:
        return folded_names[0], "name-case-insensitive"
    if len(folded_names) > 1:
        raise VscodeProfileError(f"Profile name is ambiguous: {reference!r}")

    id_matches = [item for item in profiles if item["id"] == reference]
    if len(id_matches) == 1:
        return id_matches[0], "id"

    known = [item["name"] or item["id"] for item in profiles]
    suffix = f" Known profiles: {', '.join(known)}" if known else ""
    raise VscodeProfileError(f"Profile not found: {reference!r}.{suffix}")


def require_profile_dir(profile: dict[str, Any]) -> Path:
    profile_dir = Path(profile["profileDir"]).resolve()
    if not profile.get("profileDirExists"):
        raise VscodeProfileError(
            f"Profile files do not exist locally for {profile.get('name') or profile.get('id')!r}"
        )
    return profile_dir


def command_show_profile(args: argparse.Namespace) -> None:
    user_dir = user_dir_from_args(args)
    profile, matched_by = resolve_profile_reference(user_dir, args.profile)
    profile_name = profile.get("name")
    context_issue = cli_context_issue(args)
    if matched_by == "id" and not profile_name:
        extension_context: dict[str, Any] = {
            "profile": None,
            "extensions": [],
            "error": "Extensions cannot be targeted safely because this internal ID has no verified display name.",
        }
    elif context_issue:
        extension_context = {
            "profile": profile_name,
            "extensions": [],
            "error": context_issue,
        }
    else:
        extension_context = extension_snapshot_for_profile(
            code_bin_for_variant(args.variant, args.code_bin),
            None if matched_by == "default" else profile_name,
            code_context_args(args),
        )
    print(
        json.dumps(
            {
                "userDir": str(user_dir),
                "matchedBy": matched_by,
                "profile": profile,
                "extensionContext": extension_context,
            },
            indent=2,
        )
    )


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


def collect_default_backup_items(user_dir: Path) -> list[BackupItem]:
    items = [
        BackupItem(user_dir / filename, filename)
        for filename in SAFE_DEFAULT_FILES
        if (user_dir / filename).is_file() and not (user_dir / filename).is_symlink()
    ]
    items.extend(collect_snippet_items(user_dir / "snippets", "snippets"))
    return items


def collect_backup_items(
    user_dir: Path, profile_id: str | None, default_only: bool = False
) -> list[BackupItem]:
    if profile_id:
        return collect_profile_backup_items(user_dir, profile_id)
    items = collect_default_backup_items(user_dir)
    if default_only:
        return items
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
    default_only: bool = False,
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
    if default_only:
        return [extension_snapshot_for_profile(code_bin, None, context)]
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
    default_only: bool = False,
    reason: str = "manual",
) -> tuple[Path, dict[str, Any]]:
    if profile_id and default_only:
        raise VscodeProfileError(
            "A backup cannot target a named and Default Profile together"
        )
    if profile_id:
        profile_id = validate_profile_id(profile_id)
    items = collect_backup_items(user_dir, profile_id, default_only=default_only)
    extension_snapshots = collect_extension_snapshots(
        args, user_dir, profile_id, profile_name, default_only=default_only
    )
    if not items and not extension_snapshots:
        raise VscodeProfileError(f"Nothing found to back up under {user_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    if profile_id:
        label = f"{args.variant}-profile-{profile_id}"
        scope = "profile"
    elif default_only:
        label = f"{args.variant}-default-profile"
        scope = "default-profile"
    else:
        label = f"{args.variant}-user-config"
        scope = "user-config"
    zip_path = out_dir / f"{label}-{now_stamp()}.zip"
    manifest = {
        "formatVersion": 1,
        "createdAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "variant": args.variant,
        "scope": scope,
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
    profile_id: str | None = None
    profile_name: str | None = None
    matched_by: str | None = None
    default_only = False
    if args.profile:
        profile, matched_by = resolve_profile_reference(user_dir, args.profile)
        if matched_by == "default":
            require_profile_dir(profile)
            default_only = True
            profile_name = "Default"
        else:
            require_profile_dir(profile)
            profile_id = validate_profile_id(profile["id"])
            profile_name = profile.get("name")
    out_dir = expand_path(args.out or (Path.home() / "Desktop" / BACKUP_DIR_NAME))
    zip_path, manifest = create_backup_archive(
        args,
        user_dir,
        out_dir,
        profile_id=profile_id,
        profile_name=profile_name,
        default_only=default_only,
    )
    print(
        json.dumps(
            {"backup": str(zip_path), "matchedBy": matched_by, "manifest": manifest},
            indent=2,
        )
    )


def command_validate(args: argparse.Namespace) -> None:
    path = expand_path(args.file)
    value = load_jsonc(path)
    print(
        json.dumps(
            {"file": str(path), "valid": True, "type": type(value).__name__}, indent=2
        )
    )


def command_merge_settings(args: argparse.Namespace) -> None:
    if args.profile:
        user_dir = user_dir_from_args(args)
        profile, matched_by = resolve_profile_reference(user_dir, args.profile)
        profile_dir = require_profile_dir(profile)
        path = profile_dir / "settings.json"
    else:
        matched_by = "file"
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
                "profile": args.profile,
                "matchedBy": matched_by,
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


@dataclass
class FileState:
    path: Path
    existed: bool
    content: bytes | None
    mode: int | None


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
        profile, matched_by = resolve_profile_reference(user_dir, args.profile)
        snapshot["matchedBy"] = matched_by
        snapshot["resolvedProfile"] = profile
        profile_name = profile.get("name")
        context_issue = cli_context_issue(args)
        if matched_by == "id" and not profile_name:
            extension_snapshot = {
                "profile": None,
                "returncode": None,
                "extensions": [],
                "error": "Extension snapshot skipped because the internal ID has no verified display name.",
            }
        elif context_issue:
            extension_snapshot = {
                "profile": profile_name,
                "returncode": None,
                "extensions": [],
                "error": f"Extension snapshot skipped: {context_issue}",
            }
        else:
            extension_snapshot = extension_snapshot_for_profile(
                code_bin,
                None if matched_by == "default" else profile_name,
                code_context_args(args),
            )
        snapshot["extensionSnapshot"] = extension_snapshot
        profile_dir = require_profile_dir(profile)
        snapshot["profileFiles"] = snapshot_profile_files(
            profile_dir, include_mcp=args.include_mcp
        )
    elif args.include_default_settings:
        snapshot["defaultSettings"] = load_jsonc(user_dir / "settings.json", default={})
    if args.out:
        out = expand_path(args.out)
        write_json(out, snapshot)
        print(out)
    else:
        print(json.dumps(snapshot, indent=2))


def command_open_profile(args: argparse.Namespace) -> None:
    require_cli_context(args)
    workspace = expand_path(args.workspace) if args.workspace else None
    cmd = [code_bin_for_variant(args.variant, args.code_bin)]
    if workspace:
        cmd.append(str(workspace))
    cmd += [*code_context_args(args), "--profile", args.profile]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dryRun": True,
                    "profile": args.profile,
                    "workspace": str(workspace) if workspace else None,
                    "command": cmd,
                },
                indent=2,
            )
        )
        return
    if workspace:
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
            {
                "opened": True,
                "profile": args.profile,
                "workspace": str(workspace) if workspace else None,
            },
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
    if scope == "default-profile":
        if len(parts) == 1 and parts[0] in SAFE_DEFAULT_FILES:
            return
        if len(parts) >= 2 and parts[0] == "snippets":
            return
        raise VscodeProfileError(f"Unexpected Default Profile backup member: {name}")
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
    elif scope not in {"default-profile", "user-config"}:
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
                default_only=manifest.get("scope") == "default-profile",
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
        description="Inspect and safely maintain VS Code profiles by display name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              python3 scripts/vscode_profile_manager.py doctor --variant code
              python3 scripts/vscode_profile_manager.py show-profile --profile "Python"
              python3 scripts/vscode_profile_manager.py backup --profile "Python"
              python3 scripts/vscode_profile_manager.py merge-settings --profile "Python" --set-json '{"editor.formatOnSave":true}'
              python3 scripts/vscode_profile_manager.py list-extensions --profile "Python" --show-versions
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
        "list-profiles", help="List known profile names, IDs, and configuration paths"
    )
    p.set_defaults(func=command_list_profiles)

    p = sub.add_parser(
        "show-profile",
        help="Resolve one profile name and show its files and extension context",
    )
    p.add_argument("--profile", required=True)
    p.set_defaults(func=command_show_profile)

    p = sub.add_parser(
        "doctor",
        help="Check Python, VS Code CLI, paths, and profile discovery without changing state",
    )
    p.set_defaults(func=command_doctor)

    p = sub.add_parser(
        "open-profile",
        help="Create or open a profile through the official VS Code CLI",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--workspace")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_open_profile)

    p = sub.add_parser(
        "backup", help="Create a timestamped zip backup of profile configuration"
    )
    p.add_argument(
        "--profile",
        help="Profile display name; omit to back up Default and all named profiles",
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
        "merge-settings", help="Safely merge top-level settings into a named profile"
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--profile", help="Profile display name or exact ID fallback")
    target.add_argument(
        "--file", help="Exact settings.json path for Default/custom file-only work"
    )
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

    p = sub.add_parser(
        "snapshot", help="Create a JSON snapshot of one profile's files and extensions"
    )
    p.add_argument("--profile")
    p.add_argument(
        "--include-default-settings",
        action="store_true",
        help="Include Default settings when no --profile is supplied",
    )
    p.add_argument(
        "--include-mcp",
        action="store_true",
        help="Include potentially sensitive mcp.json content",
    )
    p.add_argument("--out")
    p.set_defaults(func=command_snapshot)

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
