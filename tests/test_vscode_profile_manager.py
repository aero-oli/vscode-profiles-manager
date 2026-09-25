from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import platform
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vscode_profile_manager.py"
module_spec = importlib.util.spec_from_file_location("vscode_profile_manager", SCRIPT)
vpm = importlib.util.module_from_spec(module_spec)
assert module_spec.loader is not None
sys.modules[module_spec.name] = vpm
module_spec.loader.exec_module(vpm)


def base_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "variant": "code",
        "user_dir": None,
        "user_data_dir": None,
        "code_bin": "code",
        "profile": None,
        "skip_extensions": True,
        "out": None,
        "backup_dir": None,
        "allow_comment_loss": False,
        "include_mcp": False,
        "include_default_settings": False,
        "force": False,
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def write_profile_names(user_dir: Path, profiles: list[dict[str, str]]) -> None:
    sync_file = user_dir / "sync" / "profiles" / "lastSyncprofiles.json"
    sync_file.parent.mkdir(parents=True, exist_ok=True)
    sync_file.write_text(
        json.dumps({"syncData": {"content": json.dumps(profiles)}}),
        encoding="utf-8",
    )


class JsoncTests(unittest.TestCase):
    def test_strip_jsonc_preserves_comment_markers_and_commas_inside_strings(
        self,
    ) -> None:
        parsed = json.loads(
            vpm.strip_jsonc(
                '{"url":"https://example.test", "literal": ",}", "items":[1,2,],}'
            )
        )

        self.assertEqual(parsed["url"], "https://example.test")
        self.assertEqual(parsed["literal"], ",}")
        self.assertEqual(parsed["items"], [1, 2])

    def test_comment_detection_ignores_strings(self) -> None:
        self.assertFalse(vpm.has_jsonc_comments('{"url":"https://example.test"}'))
        self.assertTrue(vpm.has_jsonc_comments('{// note\n"enabled":true}'))

    def test_settings_replace_is_top_level_and_deep_merge_is_explicit(self) -> None:
        base = {"files.associations": {"*.old": "old"}}

        replaced = vpm.merge_settings_data(base, {"files.associations": {}}, "replace")
        deep = vpm.merge_settings_data(base, {"files.associations": {}}, "deep")

        self.assertEqual(replaced["files.associations"], {})
        self.assertEqual(deep["files.associations"], {"*.old": "old"})

    def test_merge_settings_refuses_comment_loss_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                '{\n  // keep me\n  "editor.fontSize": 14\n}\n', encoding="utf-8"
            )
            args = base_args(
                file=str(path),
                set_json='{"editor.fontSize":16}',
                remove_key=[],
                strategy="replace",
            )

            with self.assertRaises(vpm.VscodeProfileError):
                vpm.command_merge_settings(args)

            self.assertIn("// keep me", path.read_text(encoding="utf-8"))

    def test_atomic_write_preserves_existing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{}\n", encoding="utf-8")
            os.chmod(path, 0o640)

            vpm.write_json(path, {"ok": True})

            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})


class PathAndDiscoveryTests(unittest.TestCase):
    def test_profile_id_rejects_path_components_on_every_platform(self) -> None:
        for value in ("../outside", "..\\outside", "/absolute", "", ".", ".."):
            with self.subTest(value=value), self.assertRaises(vpm.VscodeProfileError):
                vpm.validate_profile_id(value)

    def test_user_data_dir_maps_to_user_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = base_args(user_data_dir=tmp)

            self.assertEqual(vpm.user_dir_from_args(args), Path(tmp).resolve() / "User")
            self.assertEqual(
                vpm.code_context_args(args),
                ["--user-data-dir", str(Path(tmp).resolve())],
            )

    def test_custom_user_dir_cannot_be_mistaken_for_a_cli_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = base_args(
                user_dir=str(Path(tmp) / "Custom" / "User"),
                profile="Python",
                show_versions=True,
            )
            with (
                mock.patch.object(vpm.subprocess, "run") as run,
                self.assertRaisesRegex(
                    vpm.VscodeProfileError, "cannot target custom --user-dir"
                ),
            ):
                vpm.command_list_extensions(args)
            run.assert_not_called()

            isolated = base_args(
                user_dir=None, user_data_dir=str(Path(tmp) / "instance")
            )
            self.assertIsNone(vpm.cli_context_issue(isolated))

    def test_platform_default_paths(self) -> None:
        with mock.patch.object(platform, "system", return_value="Darwin"):
            self.assertTrue(
                str(vpm.user_dir_for_variant("insiders")).endswith(
                    "Code - Insiders/User"
                )
            )
        with mock.patch.object(platform, "system", return_value="Linux"):
            self.assertTrue(
                str(vpm.user_dir_for_variant("codium")).endswith(
                    ".config/VSCodium/User"
                )
            )
        with (
            mock.patch.object(platform, "system", return_value="Windows"),
            mock.patch.dict(os.environ, {"APPDATA": "C:/Users/Test/AppData/Roaming"}),
        ):
            self.assertTrue(str(vpm.user_dir_for_variant("code")).endswith("Code/User"))

    def test_discovery_reports_mcp_and_synced_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp)
            profile_dir = user_dir / "profiles" / "abc123"
            profile_dir.mkdir(parents=True)
            (profile_dir / "mcp.json").write_text('{"servers":{}}', encoding="utf-8")
            write_profile_names(user_dir, [{"id": "abc123", "name": "Python"}])

            profile = vpm.discover_profiles(user_dir)[0]

            self.assertEqual(profile["name"], "Python")
            self.assertTrue(profile["hasMcp"])
            self.assertEqual(profile["mcpFile"], str(profile_dir / "mcp.json"))

    def test_name_first_resolution_and_id_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            for profile_id in ("abc123", "unmapped"):
                (user_dir / "profiles" / profile_id).mkdir(parents=True)
            write_profile_names(user_dir, [{"id": "abc123", "name": "Python"}])

            named, named_match = vpm.resolve_profile_reference(user_dir, "python")
            by_id, id_match = vpm.resolve_profile_reference(user_dir, "unmapped")

            self.assertEqual(named["id"], "abc123")
            self.assertEqual(named_match, "name-case-insensitive")
            self.assertEqual(by_id["id"], "unmapped")
            self.assertEqual(id_match, "id")

    def test_unknown_and_ambiguous_profiles_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            for profile_id in ("one", "two"):
                (user_dir / "profiles" / profile_id).mkdir(parents=True)
            write_profile_names(
                user_dir,
                [
                    {"id": "one", "name": "Python"},
                    {"id": "two", "name": "python"},
                ],
            )

            with self.assertRaisesRegex(vpm.VscodeProfileError, "ambiguous"):
                vpm.resolve_profile_reference(user_dir, "PYTHON")
            with self.assertRaisesRegex(vpm.VscodeProfileError, "Known profiles"):
                vpm.resolve_profile_reference(user_dir, "Rust")

    def test_default_profile_resolves_to_user_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            user_dir.mkdir()

            profile, matched_by = vpm.resolve_profile_reference(user_dir, "Default")

            self.assertEqual(matched_by, "default")
            self.assertIsNone(profile["id"])
            self.assertEqual(profile["settingsFile"], str(user_dir / "settings.json"))


class DirectProfileTests(unittest.TestCase):
    def create_target(self, root: Path, name: str = "Python") -> tuple[Path, Path]:
        user_dir = root / "User"
        profile_dir = user_dir / "profiles" / "abc123"
        profile_dir.mkdir(parents=True)
        write_profile_names(user_dir, [{"id": "abc123", "name": name}])
        return user_dir, profile_dir

    def test_show_profile_reports_paths_and_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir, profile_dir = self.create_target(root)
            (profile_dir / "settings.json").write_text("{}", encoding="utf-8")
            output = io.StringIO()
            args = base_args(user_data_dir=str(root), profile="Python")

            with (
                mock.patch.object(
                    vpm,
                    "extension_snapshot_for_profile",
                    return_value={
                        "profile": "Python",
                        "returncode": 0,
                        "extensions": ["publisher.extension@1.0.0"],
                        "stderr": None,
                    },
                ),
                contextlib.redirect_stdout(output),
            ):
                vpm.command_show_profile(args)

            result = json.loads(output.getvalue())
            self.assertEqual(result["matchedBy"], "name")
            self.assertEqual(
                result["profile"]["settingsFile"],
                str((profile_dir / "settings.json").resolve()),
            )
            self.assertEqual(
                result["extensionContext"]["extensions"],
                ["publisher.extension@1.0.0"],
            )

    def test_merge_settings_resolves_profile_name_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir, profile_dir = self.create_target(root)
            settings = profile_dir / "settings.json"
            settings.write_text('{"editor.fontSize":14}\n', encoding="utf-8")
            output = io.StringIO()
            args = base_args(
                user_dir=str(user_dir),
                profile="Python",
                file=None,
                set_json='{"editor.fontSize":16}',
                remove_key=[],
                strategy="replace",
            )

            with contextlib.redirect_stdout(output):
                vpm.command_merge_settings(args)

            result = json.loads(output.getvalue())
            self.assertEqual(result["matchedBy"], "name")
            self.assertEqual(vpm.load_jsonc(settings)["editor.fontSize"], 16)
            self.assertTrue(Path(result["backup"]).is_file())

    def test_backup_resolves_name_without_exposing_id_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir, _ = self.create_target(Path(tmp))
            args = base_args(
                user_dir=str(user_dir),
                profile="Python",
                out=str(Path(tmp) / "backups"),
            )

            with (
                mock.patch.object(
                    vpm,
                    "create_backup_archive",
                    return_value=(Path(tmp) / "backup.zip", {"scope": "profile"}),
                ) as create,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                vpm.command_backup(args)

            self.assertEqual(create.call_args.kwargs["profile_id"], "abc123")
            self.assertEqual(create.call_args.kwargs["profile_name"], "Python")

    def test_backup_default_profile_is_focused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            named_dir = user_dir / "profiles" / "abc123"
            named_dir.mkdir(parents=True)
            (user_dir / "settings.json").write_text("{}", encoding="utf-8")
            (named_dir / "settings.json").write_text("{}", encoding="utf-8")
            args = base_args(
                user_dir=str(user_dir),
                profile="Default",
                out=str(Path(tmp) / "backups"),
            )

            with (
                mock.patch.object(
                    vpm,
                    "create_backup_archive",
                    return_value=(
                        Path(tmp) / "backup.zip",
                        {"scope": "default-profile"},
                    ),
                ) as create,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                vpm.command_backup(args)

            self.assertTrue(create.call_args.kwargs["default_only"])
            self.assertIsNone(create.call_args.kwargs["profile_id"])
            self.assertEqual(create.call_args.kwargs["profile_name"], "Default")

    def test_snapshot_resolves_files_and_omits_mcp_content_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, profile_dir = self.create_target(root)
            (profile_dir / "settings.json").write_text(
                '{"editor.formatOnSave":true}', encoding="utf-8"
            )
            (profile_dir / "keybindings.json").write_text(
                '[{"key":"cmd+k","command":"example"}]', encoding="utf-8"
            )
            (profile_dir / "tasks.json").write_text(
                '{"version":"2.0.0","tasks":[]}', encoding="utf-8"
            )
            snippets = profile_dir / "snippets"
            snippets.mkdir()
            (snippets / "python.json").write_text(
                '{"Print":{"prefix":"pp","body":"print($1)"}}', encoding="utf-8"
            )
            (profile_dir / "mcp.json").write_text(
                '{"servers":{"private":{"env":{"API_KEY":"secret"}}}}',
                encoding="utf-8",
            )
            args = base_args(user_data_dir=str(root), profile="Python")
            output = io.StringIO()

            with (
                mock.patch.object(
                    vpm,
                    "extension_snapshot_for_profile",
                    return_value={
                        "profile": "Python",
                        "returncode": 0,
                        "extensions": [],
                        "stderr": None,
                    },
                ),
                contextlib.redirect_stdout(output),
            ):
                vpm.command_snapshot(args)

            result = json.loads(output.getvalue())
            files = result["profileFiles"]
            self.assertTrue(files["settings.json"]["editor.formatOnSave"])
            self.assertEqual(files["keybindings.json"][0]["key"], "cmd+k")
            self.assertEqual(files["tasks.json"]["version"], "2.0.0")
            self.assertEqual(files["snippets"]["python.json"]["Print"]["prefix"], "pp")
            self.assertIn("omitted", files["mcp.json"])
            self.assertNotIn("secret", output.getvalue())

    def test_open_profile_dry_run_does_not_require_workspace(self) -> None:
        args = base_args(profile="Rust", workspace=None, dry_run=True)
        output = io.StringIO()

        with (
            mock.patch.object(vpm.subprocess, "run") as run,
            contextlib.redirect_stdout(output),
        ):
            vpm.command_open_profile(args)

        result = json.loads(output.getvalue())
        self.assertIsNone(result["workspace"])
        self.assertEqual(result["command"], ["code", "--profile", "Rust"])
        run.assert_not_called()


class BackupRestoreTests(unittest.TestCase):
    def test_safe_backup_excludes_internal_state_and_includes_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            profile_dir = user_dir / "profiles" / "abc123"
            (profile_dir / "globalStorage").mkdir(parents=True)
            (profile_dir / "settings.json").write_text("{}", encoding="utf-8")
            (profile_dir / "mcp.json").write_text('{"servers":{}}', encoding="utf-8")
            (profile_dir / "extensions.json").write_text("[]", encoding="utf-8")
            (profile_dir / "globalStorage" / "state.vscdb").write_bytes(b"sqlite")

            names = [
                item.archive_name
                for item in vpm.collect_profile_backup_items(user_dir, "abc123")
            ]

            self.assertIn("profiles/abc123/settings.json", names)
            self.assertIn("profiles/abc123/mcp.json", names)
            self.assertNotIn("profiles/abc123/extensions.json", names)
            self.assertFalse(any("state.vscdb" in name for name in names))

    def test_backup_archive_has_manifest_unique_name_and_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            out_dir = Path(tmp) / "out"
            user_dir.mkdir()
            (user_dir / "settings.json").write_text("{}", encoding="utf-8")
            args = base_args(skip_extensions=True)

            first, _ = vpm.create_backup_archive(args, user_dir, out_dir)
            second, _ = vpm.create_backup_archive(args, user_dir, out_dir)

            self.assertNotEqual(first, second)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            with zipfile.ZipFile(first) as archive:
                self.assertIn(vpm.BACKUP_MANIFEST_NAME, archive.namelist())
                self.assertIn("settings.json", archive.namelist())

    def test_default_profile_backup_excludes_named_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "User"
            named_dir = user_dir / "profiles" / "abc123"
            named_dir.mkdir(parents=True)
            (user_dir / "settings.json").write_text("{}", encoding="utf-8")
            (named_dir / "settings.json").write_text("{}", encoding="utf-8")

            archive_path, manifest = vpm.create_backup_archive(
                base_args(skip_extensions=True),
                user_dir,
                root / "backups",
                profile_name="Default",
                default_only=True,
            )

            self.assertEqual(manifest["scope"], "default-profile")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("settings.json", archive.namelist())
                self.assertFalse(
                    any(name.startswith("profiles/") for name in archive.namelist())
                )

    def test_restore_rejects_traversal_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "bad.zip"
            manifest = {
                "formatVersion": 1,
                "variant": "code",
                "scope": "user-config",
                "profileId": None,
                "files": ["../outside"],
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(vpm.BACKUP_MANIFEST_NAME, json.dumps(manifest))
                archive.writestr("../outside", "bad")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(vpm.VscodeProfileError, "Unsafe"):
                    vpm.restore_members(archive, manifest, Path(tmp) / "User")

    def test_restore_rejects_internal_state_and_duplicate_zip_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal_member = "profiles/abc123/globalStorage/state.vscdb"
            manifest = {
                "formatVersion": 1,
                "variant": "code",
                "scope": "user-config",
                "profileId": None,
                "files": [internal_member],
            }
            internal_archive = root / "internal.zip"
            with zipfile.ZipFile(internal_archive, "w") as archive:
                archive.writestr(vpm.BACKUP_MANIFEST_NAME, json.dumps(manifest))
                archive.writestr(internal_member, "sqlite")
            with zipfile.ZipFile(internal_archive) as archive:
                with self.assertRaisesRegex(
                    vpm.VscodeProfileError, "Unexpected user-config"
                ):
                    vpm.restore_members(archive, manifest, root / "User")

            duplicate_archive = root / "duplicate.zip"
            duplicate_manifest = {**manifest, "files": ["settings.json"]}
            with zipfile.ZipFile(duplicate_archive, "w") as archive:
                archive.writestr(
                    vpm.BACKUP_MANIFEST_NAME, json.dumps(duplicate_manifest)
                )
                archive.writestr("settings.json", "{}")
                with self.assertWarns(UserWarning):
                    archive.writestr("settings.json", '{"duplicate":true}')
            with zipfile.ZipFile(duplicate_archive) as archive:
                with self.assertRaisesRegex(vpm.VscodeProfileError, "duplicate member"):
                    vpm.restore_members(archive, duplicate_manifest, root / "User")

    def test_restore_previews_then_atomically_restores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            profile_dir = user_dir / "profiles" / "abc123"
            profile_dir.mkdir(parents=True)
            settings = profile_dir / "settings.json"
            settings.write_text('{"value":"current"}', encoding="utf-8")
            archive_path = Path(tmp) / "backup.zip"
            member = "profiles/abc123/settings.json"
            manifest = {
                "formatVersion": 1,
                "variant": "code",
                "scope": "profile",
                "profileId": "abc123",
                "profile": "Python",
                "files": [member],
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(vpm.BACKUP_MANIFEST_NAME, json.dumps(manifest))
                archive.writestr(member, '{"value":"restored"}\n')
            preview_args = base_args(
                archive=str(archive_path),
                user_dir=str(user_dir),
                confirm=False,
                allow_variant_mismatch=False,
                allow_profile_mismatch=False,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                vpm.command_restore(preview_args)
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["value"], "current"
            )

            confirm_args = base_args(
                archive=str(archive_path),
                user_dir=str(user_dir),
                confirm=True,
                allow_variant_mismatch=False,
                allow_profile_mismatch=False,
            )
            with mock.patch.object(
                vpm,
                "create_backup_archive",
                return_value=(Path(tmp) / "recovery.zip", {}),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    vpm.command_restore(confirm_args)

            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["value"], "restored"
            )

    def test_full_user_backup_round_trips_default_and_named_profile_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "User"
            profile_dir = user_dir / "profiles" / "abc123"
            profile_dir.mkdir(parents=True)
            default_settings = user_dir / "settings.json"
            profile_settings = profile_dir / "settings.json"
            default_settings.write_text('{"scope":"default-backup"}', encoding="utf-8")
            profile_settings.write_text('{"scope":"profile-backup"}', encoding="utf-8")
            archive, _ = vpm.create_backup_archive(
                base_args(skip_extensions=True), user_dir, root / "backups"
            )
            default_settings.write_text('{"scope":"changed"}', encoding="utf-8")
            profile_settings.write_text('{"scope":"changed"}', encoding="utf-8")
            args = base_args(
                archive=str(archive),
                user_dir=str(user_dir),
                confirm=True,
                allow_variant_mismatch=False,
                allow_profile_mismatch=False,
                backup_dir=str(root / "recovery"),
            )

            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch.object(vpm.subprocess, "run") as run,
            ):
                vpm.command_restore(args)

            self.assertEqual(
                vpm.load_jsonc(default_settings)["scope"], "default-backup"
            )
            self.assertEqual(
                vpm.load_jsonc(profile_settings)["scope"], "profile-backup"
            )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
