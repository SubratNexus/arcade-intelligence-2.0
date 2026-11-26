import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from notification_manager import _is_sendable_official_event, send_pending_alerts

TRUSTED_SOURCE = {
    "source_type": "google_cloud_community",
    "source_name": "Google Cloud Community",
    "source_domain": "discuss.google.dev",
    "source_url": "https://discuss.google.dev/t/arcade-official-update/123",
    "publisher_authority": "google_staff_group",
    "is_google_staff_author": True,
    "author_verification_method": "discourse_google_staff_group",
}


def approved_event():
    return {
        "event_id": "test-event-001",
        "title": "Swag Drop: Arcade TechPack",
        "event_type": "swag_drop",
        "verification_status": "officially_announced",
        "human_review_required": False,
        "notification_approved": True,
        "published_at": "2026-11-01T00:00:00Z",
        "verification_explanation": "Verified public Google Staff group metadata.",
        "source_urls": [TRUSTED_SOURCE["source_url"]],
        "sources": [TRUSTED_SOURCE],
        "explicit_point_mentions": [],
        "tier_mentions": ["Champion", "Legend"],
        "content_fingerprints": ["test-fingerprint"],
    }


class NotificationTests(unittest.TestCase):
    def test_event_needs_human_approval_and_trusted_source(self):
        event = approved_event()
        self.assertTrue(_is_sendable_official_event(event))
        event["human_review_required"] = True
        self.assertFalse(_is_sendable_official_event(event))
        event["human_review_required"] = False
        event["notification_approved"] = False
        self.assertFalse(_is_sendable_official_event(event))
        event["notification_approved"] = True
        event["sources"] = [{**TRUSTED_SOURCE, "author_verification_method": None}]
        self.assertFalse(_is_sendable_official_event(event))
        event["sources"] = [{**TRUSTED_SOURCE, "source_url": "https://attacker.example/fake"}]
        self.assertFalse(_is_sendable_official_event(event))

    def test_unreviewed_official_candidate_never_sends_email(self):
        event = approved_event()
        event["human_review_required"] = True
        event["notification_approved"] = False
        required_env = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "not-a-real-password",
            "ALERT_EMAIL_TO": "recipient@example.test",
            "ALERT_EMAIL_FROM": "sender@example.test",
        }
        with tempfile.TemporaryDirectory() as temp:
            events_file = Path(temp) / "events.json"
            state_file = Path(temp) / "notification-state.json"
            events_file.write_text(json.dumps({"events": [event]}), encoding="utf-8")
            with patch("notification_manager.EVENTS_FILE", events_file), \
                 patch("notification_manager.STATE_FILE", state_file), \
                 patch.dict(os.environ, required_env, clear=False), \
                 patch("notification_manager.smtplib.SMTP_SSL") as smtp_ssl:
                result = send_pending_alerts()
            self.assertEqual(result["sent"], 0)
            self.assertEqual(result["official_candidates_waiting_for_review"], 1)
            smtp_ssl.assert_not_called()
            self.assertFalse(state_file.exists())

    def test_reviewed_and_approved_official_event_can_send(self):
        event = approved_event()
        required_env = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "not-a-real-password",
            "ALERT_EMAIL_TO": "recipient@example.test",
            "ALERT_EMAIL_FROM": "sender@example.test",
        }
        with tempfile.TemporaryDirectory() as temp:
            events_file = Path(temp) / "events.json"
            state_file = Path(temp) / "notification-state.json"
            events_file.write_text(json.dumps({"events": [event]}), encoding="utf-8")
            smtp_server = MagicMock()
            smtp_factory = MagicMock()
            smtp_factory.return_value.__enter__.return_value = smtp_server
            with patch("notification_manager.EVENTS_FILE", events_file), \
                 patch("notification_manager.STATE_FILE", state_file), \
                 patch.dict(os.environ, required_env, clear=False), \
                 patch("notification_manager.smtplib.SMTP_SSL", smtp_factory):
                result = send_pending_alerts()
            self.assertEqual(result["sent"], 1)
            smtp_server.login.assert_called_once()
            smtp_server.send_message.assert_called_once()
            self.assertTrue(state_file.exists())


if __name__ == "__main__":
    unittest.main()
