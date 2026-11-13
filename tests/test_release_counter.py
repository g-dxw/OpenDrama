"""Discover release numbers only in disposable Git repositories; never publish."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
SPEC = importlib.util.spec_from_file_location("jjtv_release_counter", SOURCE)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
TODAY = datetime(2026, 11, 13, tzinfo=timezone.utc)


class ReleaseCounterTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jjtv-release-counter-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "main"
        self.root.mkdir()
        # An outer WSL command may bind Git to the real task worktree. Strip
        # every Git override before even initializing a disposable repository.
        self.git_environment = {key: value for key, value in os.environ.items()
                                if not key.startswith("GIT_")}
        self.git_environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        environment = patch.dict(os.environ, self.git_environment, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.command(self.root, "init", "--initial-branch=main")
        self.command(self.root, "config", "user.name", "Counter Test")
        self.command(self.root, "config", "user.email", "counter@example.invalid")
        self.command(self.root, "config", "commit.gpgsign", "false")
        self.command(self.root, "config", "core.logAllRefUpdates", "true")
        self.command(self.root, "commit", "--allow-empty", "-m", "baseline")
        self.baseline = self.command(self.root, "rev-parse", "HEAD")
        for attribute, value in (("REPO", self.root),
                                 ("RECORDS_DIR", self.root / "deployments" / "releases"),
                                 ("now", lambda: TODAY)):
            override = patch.object(release, attribute, value)
            override.start()
            self.addCleanup(override.stop)

    def command(self, root, *args):
        self.assertTrue(root.resolve().is_relative_to(Path(self.temporary.name).resolve()))
        options = dict(cwd=root, env=self.git_environment, capture_output=True, text=True, check=True)
        if args[0] != "init":
            actual = subprocess.run(["git", "rev-parse", "--show-toplevel"], **options).stdout.strip()
            self.assertEqual(Path(actual).resolve(), root.resolve())
        return subprocess.run(["git", *args], **options).stdout.strip()

    def record(self, root, version, *, commit=False):
        path = root / "deployments" / "releases" / (version + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": version, "status": "failed"}) + "\n")
        if commit:
            self.command(root, "add", "--", str(path.relative_to(root)))
            self.command(root, "commit", "-m", "failed release ledger")
        return path

    def other_worktree(self):
        other = Path(self.temporary.name) / "other"
        self.command(self.root, "worktree", "add", "-b", "other-release", str(other))
        return other

    def test_existing_tags_and_local_records_still_reserve_numbers(self):
        self.command(self.root, "tag", "v2026.11.13.3")
        self.record(self.root, "v2026.11.13.5")
        self.record(self.root, "v2026.11.12.999", commit=True)
        self.record(self.root, "v2026.11.13.999-invalid", commit=True)
        self.assertEqual(release.next_version(), "v2026.11.13.6")

    def test_other_worktree_failed_ledger_reserves_deleted_tag(self):
        other = self.other_worktree()
        self.record(other, "v2026.11.13.16", commit=True)
        self.command(other, "tag", "v2026.11.13.16")
        self.command(other, "tag", "-d", "v2026.11.13.16")
        self.assertFalse((self.root / "deployments/releases/v2026.11.13.16.json").exists())
        self.assertEqual(self.command(self.root, "tag", "-l", "v2026.11.13.16"), "")
        self.assertEqual(release.next_version(), "v2026.11.13.17")

    def test_other_worktree_reflog_reserves_number_after_branch_deletion(self):
        other = self.other_worktree()
        self.record(other, "v2026.11.13.19", commit=True)
        abandoned = self.command(other, "rev-parse", "HEAD")
        self.command(other, "checkout", "--detach", self.baseline)
        self.command(self.root, "branch", "-D", "other-release")
        self.assertNotIn(abandoned, self.command(self.root, "log", "--all", "--format=%H"))
        self.assertIn(abandoned, self.command(self.root, "log", "--all", "--reflog", "--format=%H"))
        self.assertFalse((other / "deployments/releases/v2026.11.13.19.json").exists())
        self.assertEqual(release.next_version(), "v2026.11.13.20")

    def test_history_read_failure_does_not_reuse_a_number(self):
        def broken_git(*args):
            if args[0] == "log":
                raise release.ReleaseError("synthetic history failure")
            return ""
        with patch.object(release, "git", side_effect=broken_git):
            with self.assertRaisesRegex(release.ReleaseError, "synthetic history failure"):
                release.next_version()


if __name__ == "__main__":
    unittest.main()
