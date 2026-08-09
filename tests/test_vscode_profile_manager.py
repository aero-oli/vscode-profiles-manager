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
        "profile_id": None,
        "skip_extensions": True,
        "out": None,
        "backup_dir": None,
        "allow_unverified_profile": False,
        "allow_comment_loss": False,
        "confirm_destructive": False,
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
            args = argparse.Namespace(
                file=str(path),
                set_json='{"editor.fontSize":16}',
                remove_key=[],
                strategy="replace",
                dry_run=False,
                allow_comment_loss=False,
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

    def test_target_resolution_rejects_name_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            (user_dir / "profiles" / "abc123").mkdir(parents=True)
            write_profile_names(user_dir, [{"id": "abc123", "name": "Actual"}])

            with self.assertRaisesRegex(vpm.VscodeProfileError, "identity mismatch"):
                vpm.resolve_profile_target(
                    {"profile": "Wrong", "profileId": "abc123", "settings": {"x": 1}},
                    user_dir,
                )

    def test_target_resolution_rejects_external_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            external = Path(tmp) / "elsewhere" / "settings.json"
            external.parent.mkdir(parents=True)

            with self.assertRaisesRegex(vpm.VscodeProfileError, "escapes"):
                vpm.resolve_profile_target(
                    {
                        "profile": "Python",
                        "settingsFile": str(external),
                        "settings": {"x": 1},
                    },
                    user_dir,
                )

    def test_unverified_profile_id_requires_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            (user_dir / "profiles" / "abc123").mkdir(parents=True)
            spec = {"profile": "Python", "profileId": "abc123", "settings": {"x": 1}}

            with self.assertRaisesRegex(vpm.VscodeProfileError, "Could not verify"):
                vpm.resolve_profile_target(spec, user_dir)
            self.assertEqual(
                vpm.resolve_profile_target(spec, user_dir, allow_unverified=True),
                (user_dir / "profiles" / "abc123").resolve(),
            )


class ManifestTests(unittest.TestCase):
    def test_unknown_fields_and_duplicates_are_rejected(self) -> None:
        with self.assertRaisesRegex(vpm.VscodeProfileError, "Unknown"):
            vpm.validate_manifest({"profile": "Python", "surprise": True})
        with self.assertRaisesRegex(vpm.VscodeProfileError, "duplicates"):
            vpm.validate_manifest({"profile": "Python", "extensions": ["a.b", "a.b"]})

    def test_literal_mcp_secret_is_warned_but_variable_is_not(self) -> None:
        warnings = vpm.validate_manifest(
            {
                "profile": "Python",
                "mcpServers": {
                    "unsafe": {"env": {"API_KEY": "literal"}},
                    "safe": {"env": {"API_KEY": "${env:API_KEY}"}},
                },
            }
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("unsafe", warnings[0])

    def test_manifest_rejects_vsix_and_case_insensitive_extension_duplicates(
        self,
    ) -> None:
        with self.assertRaisesRegex(vpm.VscodeProfileError, "VSIX"):
            vpm.validate_manifest(
                {"profile": "Python", "extensions": ["/tmp/example.vsix"]}
            )
        with self.assertRaisesRegex(vpm.VscodeProfileError, "same extension ID"):
            vpm.validate_manifest(
                {
                    "profile": "Python",
                    "extensions": ["Publisher.Extension", "publisher.extension@1.2.3"],
                }
            )
        with self.assertRaisesRegex(vpm.VscodeProfileError, "installed and removed"):
            vpm.validate_manifest(
                {
                    "profile": "Python",
                    "extensions": ["Publisher.Extension@1.2.3"],
                    "removeExtensions": ["publisher.extension"],
                }
            )

    def test_mcp_plan_merges_and_removes_named_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            (profile_dir / "mcp.json").write_text(
                json.dumps(
                    {
                        "servers": {
                            "keep": {"url": "https://old"},
                            "remove": {"command": "bad"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            spec = {
                "profile": "Python",
                "mcpServers": {"keep": {"url": "https://new"}},
                "removeMcpServers": ["remove"],
            }

            planned = vpm.plan_profile_files(
                spec, profile_dir, allow_comment_loss=False
            )

            self.assertEqual(
                planned[0].after["servers"], {"keep": {"url": "https://new"}}
            )


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


class ApplySpecTests(unittest.TestCase):
    def create_target(self, root: Path, name: str = "Python") -> tuple[Path, Path]:
        user_dir = root / "User"
        profile_dir = user_dir / "profiles" / "abc123"
        profile_dir.mkdir(parents=True)
        write_profile_names(user_dir, [{"id": "abc123", "name": name}])
        return user_dir, profile_dir

    def write_spec(self, root: Path, value: dict[str, object]) -> Path:
        path = root / "profile.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_apply_preflights_missing_target_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_path = self.write_spec(
                root,
                {
                    "profile": "Python",
                    "extensions": ["ms-python.python"],
                    "settings": {"x": 1},
                },
            )
            args = base_args(spec=str(spec_path), user_dir=str(root / "User"))

            with (
                mock.patch.object(vpm.subprocess, "run") as run,
                self.assertRaises(vpm.VscodeProfileError),
            ):
                vpm.command_apply_spec(args)

            run.assert_not_called()

    def test_apply_requires_confirmation_for_removals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir, _ = self.create_target(root)
            spec_path = self.write_spec(
                root,
                {
                    "profile": "Python",
                    "profileId": "abc123",
                    "removeSettings": ["editor.fontSize"],
                },
            )
            args = base_args(spec=str(spec_path), user_dir=str(user_dir))

            with (
                mock.patch.object(vpm, "create_backup_archive") as backup,
                self.assertRaisesRegex(vpm.VscodeProfileError, "confirm-destructive"),
            ):
                vpm.command_apply_spec(args)

            backup.assert_not_called()

    def test_apply_writes_files_without_opening_a_gui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir, profile_dir = self.create_target(root)
            spec_path = self.write_spec(
                root,
                {
                    "profile": "Python",
                    "profileId": "abc123",
                    "settings": {"editor.formatOnSave": True},
                    "keybindings": [
                        {
                            "key": "cmd+k",
                            "command": "workbench.action.clearEditorHistory",
                        }
                    ],
                    "tasks": {"version": "2.0.0", "tasks": []},
                    "snippets": {
                        "python.json": {"Print": {"prefix": "pp", "body": "print($1)"}}
                    },
                    "mcpServers": {
                        "docs": {"type": "http", "url": "https://example.test/mcp"}
                    },
                },
            )
            args = base_args(
                spec=str(spec_path),
                user_dir=None,
                user_data_dir=str(root),
                backup_dir=str(root / "backups"),
            )

            with mock.patch.object(
                vpm, "create_backup_archive", return_value=(root / "recovery.zip", {})
            ):
                with mock.patch.object(vpm.subprocess, "run") as run:
                    with contextlib.redirect_stdout(io.StringIO()):
                        vpm.command_apply_spec(args)

            run.assert_not_called()
            self.assertTrue(
                vpm.load_jsonc(profile_dir / "settings.json")["editor.formatOnSave"]
            )
            self.assertEqual(
                vpm.load_jsonc(profile_dir / "keybindings.json")[0]["key"], "cmd+k"
            )
            self.assertEqual(
                vpm.load_jsonc(profile_dir / "tasks.json")["version"], "2.0.0"
            )
            self.assertEqual(
                vpm.load_jsonc(profile_dir / "snippets" / "python.json")["Print"][
                    "prefix"
                ],
                "pp",
            )
            self.assertIn("docs", vpm.load_jsonc(profile_dir / "mcp.json")["servers"])

    def test_apply_rolls_back_file_when_extension_change_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir, profile_dir = self.create_target(root)
            settings = profile_dir / "settings.json"
            settings.write_text('{"editor.fontSize":14}\n', encoding="utf-8")
            spec_path = self.write_spec(
                root,
                {
                    "profile": "Python",
                    "profileId": "abc123",
                    "settings": {"editor.fontSize": 18},
                    "extensions": ["publisher.extension"],
                },
            )
            args = base_args(
                spec=str(spec_path),
                user_dir=None,
                user_data_dir=str(root),
                backup_dir=str(root / "backups"),
            )
            initial = {"returncode": 0, "extensions": [], "stderr": None}

            with (
                mock.patch.object(
                    vpm, "extension_snapshot_for_profile", return_value=initial
                ),
                mock.patch.object(
                    vpm,
                    "create_backup_archive",
                    return_value=(root / "recovery.zip", {}),
                ),
                mock.patch.object(
                    vpm,
                    "run_extension_change",
                    side_effect=vpm.VscodeProfileError("install failed"),
                ),
                mock.patch.object(vpm, "rollback_touched_extensions", return_value=[]),
            ):
                with self.assertRaisesRegex(
                    vpm.VscodeProfileError, "rollback attempted"
                ):
                    with contextlib.redirect_stdout(io.StringIO()):
                        vpm.command_apply_spec(args)

            self.assertEqual(vpm.load_jsonc(settings)["editor.fontSize"], 14)

    def test_dry_run_is_read_only_and_reports_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir, profile_dir = self.create_target(root)
            settings = profile_dir / "settings.json"
            settings.write_text('{"editor.fontSize":14}\n', encoding="utf-8")
            spec_path = self.write_spec(
                root,
                {
                    "profile": "Python",
                    "profileId": "abc123",
                    "settings": {"editor.fontSize": 18},
                },
            )
            args = base_args(spec=str(spec_path), user_dir=str(user_dir), dry_run=True)
            output = io.StringIO()

            with (
                contextlib.redirect_stdout(output),
                mock.patch.object(vpm.subprocess, "run") as run,
            ):
                vpm.command_apply_spec(args)

            run.assert_not_called()
            self.assertIn("editor.fontSize", output.getvalue())
            self.assertEqual(vpm.load_jsonc(settings)["editor.fontSize"], 14)

    def test_dry_run_explains_when_profile_file_target_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_path = self.write_spec(
                root, {"profile": "Python", "settings": {"editor.fontSize": 18}}
            )
            args = base_args(
                spec=str(spec_path), user_dir=str(root / "User"), dry_run=True
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                vpm.command_apply_spec(args)

            result = json.loads(output.getvalue())
            self.assertIsNone(result["profileDir"])
            self.assertIn("need profileId or settingsFile", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
