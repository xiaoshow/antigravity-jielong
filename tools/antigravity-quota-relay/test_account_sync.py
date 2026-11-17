"""Account selection regression tests. Never access real credentials or macOS Keychain."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import multi_hud as hud


def token(slot):
    data = {"token": {"refresh_token": f"test-refresh-{slot}"}}
    return "go-keyring-base64:" + base64.b64encode(json.dumps(data).encode()).decode()


class AccountSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        for slot in ("2", "3", "19"):
            (self.vault / f"acc{slot}.token").write_text(token(slot))
        self.marker = self.vault / ".active_slot"
        self.marker.write_text("3")
        self.patch = patch.multiple(hud, VAULT_DIR=str(self.vault),
                                    ROTATING_FILE=str(self.vault / ".rotating"),
                                    LOCK_FILE=str(self.vault / ".login_lock"))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.app = hud.MultiAccountHUD.__new__(hud.MultiAccountHUD)
        self.app.ACTIVE_FILE = str(self.marker)
        self.app.active_slot = "3"
        self.app.switching_slot = False
        self.app.root = Mock()
        self.app.setup_ui = Mock()
        self.app.account_emails = {}
        self.app.read_live_blob = Mock(return_value=token("3"))
        self.app.write_live_blob = Mock()
        self.app.save_active_slot = Mock(side_effect=AssertionError("poll must not save selection"))
        self.app.apply_slot_credentials = Mock(side_effect=AssertionError("poll must not switch accounts"))
        self.app.trace_write = Mock()

    def test_old_session_writeback_repairs_credentials_without_switching(self):
        self.app.read_live_blob.return_value = token("2")
        for _ in range(3):
            self.assertEqual(self.app.adopt_live(), token("3"))
        self.assertEqual(self.app.active_slot, "3")
        self.assertEqual(self.marker.read_text(), "3")
        self.assertEqual(self.app.write_live_blob.call_count, 3)
        self.app.write_live_blob.assert_called_with(token("3"))
        self.assertFalse((self.vault / "slot_switch_history.jsonl").exists())

    def test_external_explicit_switch_follows_latest_choice(self):
        self.marker.write_text("19")
        self.app.read_live_blob.return_value = token("19")
        self.assertEqual(self.app.adopt_live(), token("19"))
        self.assertEqual(self.app.active_slot, "19")
        self.app.root.after.assert_called_once_with(0, self.app.setup_ui)
        self.app.write_live_blob.assert_not_called()

    def test_transient_missing_or_invalid_marker_never_adopts_keychain(self):
        self.app.read_live_blob.return_value = token("2")
        for content in ("", "invalid", "41"):
            self.marker.write_text(content)
            self.assertEqual(self.app.adopt_live(), "")
            self.assertEqual(self.app.active_slot, "3")
        self.marker.unlink()
        self.assertEqual(self.app.adopt_live(), "")
        self.app.read_live_blob.assert_not_called()
        self.app.write_live_blob.assert_not_called()

    def test_missing_or_empty_selected_token_keeps_selection(self):
        for missing in (False, True):
            if missing:
                (self.vault / "acc3.token").unlink()
            else:
                (self.vault / "acc3.token").write_text("")
            self.assertEqual(self.app.adopt_live(), "")
        self.assertEqual(self.app.active_slot, "3")
        self.app.write_live_blob.assert_not_called()

    def test_corrupt_selected_token_cannot_replace_live_credentials(self):
        (self.vault / "acc3.token").write_text("invalid")
        self.app.read_live_blob.return_value = token("2")
        self.assertEqual(self.app.adopt_live(), "")
        self.app.write_live_blob.assert_not_called()

    def test_new_switch_during_keychain_read_is_not_overwritten(self):
        def stale_read():
            self.marker.write_text("19")
            return token("2")
        self.app.read_live_blob.side_effect = stale_read
        self.app.adopt_live()
        self.assertEqual(self.marker.read_text(), "19")
        self.app.write_live_blob.assert_not_called()

    def test_matching_or_unknown_identity_is_not_rewritten(self):
        for blob in (token("3"), token("unknown"), "", "invalid"):
            self.app.read_live_blob.return_value = blob
            self.app.adopt_live()
        self.app.write_live_blob.assert_not_called()

    def test_login_rotation_and_switch_locks_prevent_repair(self):
        self.app.read_live_blob.return_value = token("2")
        for name in (".rotating", ".login_lock"):
            path = self.vault / name
            path.touch()
            self.app.adopt_live()
            path.unlink()
        self.app.switching_slot = True
        self.app.adopt_live()
        self.app.write_live_blob.assert_not_called()
        self.app.read_live_blob.assert_not_called()

    def test_delayed_switch_worker_cannot_republish_previous_selection(self):
        self.marker.write_text("19")
        with patch.object(hud.subprocess, "run") as run:
            self.assertFalse(hud.MultiAccountHUD.apply_slot_credentials(self.app, "3"))
        run.assert_not_called()
        self.assertEqual(self.marker.read_text(), "19")

    def test_applying_credentials_does_not_publish_another_switch_event(self):
        with patch.object(hud.subprocess, "run"), \
                patch.object(hud, "STANDALONE_TOKEN_FILE", str(self.vault / "standalone")), \
                patch.object(hud, "CLI_TOKEN_FILE", str(self.vault / "cli")), \
                patch.object(hud, "APP_STORAGE", str(self.vault / "absent-storage")):
            self.assertTrue(hud.MultiAccountHUD.apply_slot_credentials(self.app, "3"))
        self.assertEqual(self.marker.read_text(), "3")
        self.app.save_active_slot.assert_not_called()

    def test_selection_save_uses_atomic_replace(self):
        app = self.app
        with patch.object(hud.os, "replace", wraps=hud.os.replace) as replace:
            hud.MultiAccountHUD.save_active_slot(app, "19")
        replace.assert_called_once()
        self.assertEqual(self.marker.read_text(), "19")
        self.assertEqual(list(self.vault.glob(".active_slot-*")), [])


if __name__ == "__main__":
    unittest.main()
