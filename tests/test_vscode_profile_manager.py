from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vscode_profile_manager.py"
spec = importlib.util.spec_from_file_location("vscode_profile_manager", SCRIPT)
vpm = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(vpm)


class JsoncTests(unittest.TestCase):
    def test_strip_jsonc_preserves_commas_before_braces_inside_strings(self) -> None:
        parsed = json.loads(vpm.strip_jsonc('{"literal": ",}", "items": [1, 2,],}'))

        self.assertEqual(parsed["literal"], ",}")
        self.assertEqual(parsed["items"], [1, 2])


class ProfileDiscoveryTests(unittest.TestCase):
    def test_list_profiles_combines_profile_dirs_with_synced_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp)
            (user_dir / "profiles" / "abc123").mkdir(parents=True)
            (user_dir / "profiles" / "xyz789").mkdir(parents=True)
            sync_file = user_dir / "sync" / "profiles" / "lastSyncprofiles.json"
            sync_file.parent.mkdir(parents=True)
            sync_file.write_text(
                json.dumps(
                    {
                        "syncData": {
                            "content": json.dumps(
                                [
                                    {"id": "abc123", "name": "Python"},
                                    {"id": "missing", "name": "Old Profile"},
                                ]
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )

            profiles = vpm.discover_profiles(user_dir)

        by_id = {profile["id"]: profile for profile in profiles}
        self.assertEqual(by_id["abc123"]["name"], "Python")
        self.assertEqual(by_id["abc123"]["settingsFile"], str(user_dir / "profiles" / "abc123" / "settings.json"))
        self.assertIsNone(by_id["xyz789"]["name"])
        self.assertEqual(by_id["missing"]["source"], "settings-sync")


class ApplySpecTests(unittest.TestCase):
    def test_apply_spec_preflights_missing_profile_target_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "profile.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "profile": "Python",
                        "workspace": str(Path(tmp) / "workspace"),
                        "extensions": ["ms-python.python"],
                        "settings": {"editor.formatOnSave": True},
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                spec=str(spec_path),
                variant="code",
                code_bin="code",
                user_dir=str(Path(tmp) / "User"),
                dry_run=False,
                force=False,
                continue_on_error=False,
            )

            with mock.patch.object(vpm.subprocess, "run") as run:
                with self.assertRaises(vpm.VscodeProfileError):
                    vpm.command_apply_spec(args)

        run.assert_not_called()

    def test_apply_spec_writes_profile_data_files_when_target_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / "User"
            profile_dir = user_dir / "profiles" / "abc123"
            profile_dir.mkdir(parents=True)
            spec_path = Path(tmp) / "profile.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "profile": "Python",
                        "workspace": str(Path(tmp) / "workspace"),
                        "profileId": "abc123",
                        "settings": {"editor.formatOnSave": True},
                        "keybindings": [{"key": "cmd+k", "command": "workbench.action.clearEditorHistory"}],
                        "tasks": {"version": "2.0.0", "tasks": []},
                        "snippets": {"python.json": {"Print": {"prefix": "pp", "body": "print($1)"}}},
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                spec=str(spec_path),
                variant="code",
                code_bin="code",
                user_dir=str(user_dir),
                dry_run=False,
                force=False,
                continue_on_error=False,
            )

            with mock.patch.object(vpm.subprocess, "run") as run:
                run.return_value.returncode = 0
                with contextlib.redirect_stdout(io.StringIO()):
                    vpm.command_apply_spec(args)

            self.assertEqual(vpm.load_jsonc(profile_dir / "settings.json")["editor.formatOnSave"], True)
            self.assertEqual(vpm.load_jsonc(profile_dir / "keybindings.json")[0]["key"], "cmd+k")
            self.assertEqual(vpm.load_jsonc(profile_dir / "tasks.json")["version"], "2.0.0")
            self.assertEqual(vpm.load_jsonc(profile_dir / "snippets" / "python.json")["Print"]["prefix"], "pp")


if __name__ == "__main__":
    unittest.main()
