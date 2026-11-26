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
from youtube_monitor import (
    _AllowlistedYouTubeRedirectHandler,
    _safe_api_url,
    api_get,
    run as run_youtube_monitor,
)
import urllib.error
import urllib.request


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

    def test_youtube_api_url_allowlist_and_redirect_guard(self):
        self.assertTrue(_safe_api_url("https://www.googleapis.com/youtube/v3/channels"))
        self.assertFalse(_safe_api_url("https://attacker.example/youtube/v3/channels"))
        self.assertFalse(_safe_api_url("https://www.googleapis.com:444/youtube/v3/channels"))
        request = urllib.request.Request(
            "https://www.googleapis.com/youtube/v3/channels?key=example-secret"
        )
        handler = _AllowlistedYouTubeRedirectHandler()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://attacker.example/collect"
            )
        accepted = handler.redirect_request(
            request, None, 302, "Found", {}, "https://www.googleapis.com/youtube/v3/channels2"
        )
        self.assertEqual(accepted.full_url, "https://www.googleapis.com/youtube/v3/channels2")

    def test_youtube_api_rejects_unapproved_endpoint_before_network(self):
        with patch("youtube_monitor._API_OPENER.open") as api_open:
            with self.assertRaises(ValueError):
                api_get("search", {"key": "dummy"})
            api_open.assert_not_called()

    def test_youtube_api_errors_never_echo_api_key(self):
        secret_marker = "TEST-SECRET-DO-NOT-LEAK"
        url = "https://www.googleapis.com/youtube/v3/channels?key=" + secret_marker
        error = urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
        with patch("youtube_monitor._API_OPENER.open", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                api_get("channels", {"part": "contentDetails", "id": "UC12345678901234567890", "key": secret_marker})
        self.assertNotIn(secret_marker, str(caught.exception))

    def test_youtube_monitor_discovers_video_metadata_with_mocked_api(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources = base / "sources.json"
            state = base / "state.json"
            output = base / "youtube.json"
            run_file = base / "last-youtube-run.json"
            channel_id = "UCJS9pqu9BzkAMNTmzNMNhvg"
            sources.write_text(json.dumps({
                "youtube_keywords": ["Google Skills Arcade"],
                "youtube_channels": [{
                    "id": "google-cloud-tech",
                    "name": "Google Cloud Tech",
                    "channel_id": channel_id,
                    "enabled": True,
                }],
            }), encoding="utf-8")
            state.write_text('{"schema_version": 1, "topics": {}, "youtube_channels": {}}', encoding="utf-8")

            def fake_api_get(endpoint, _params):
                if endpoint == "channels":
                    return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU123456789"}}}]}
                if endpoint == "playlistItems":
                    return {"items": [{
                        "snippet": {
                            "title": "New Arcade Games for November 2026",
                            "description": "Google Skills Arcade announcements and games.",
                            "publishedAt": "2026-11-01T00:00:00Z",
                            "channelTitle": "Google Cloud Tech",
                            "resourceId": {"videoId": "abcdefghijk"},
                            "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"}},
                        },
                    }]}
                raise AssertionError(f"Unexpected API endpoint: {endpoint}")

            with patch("youtube_monitor.SOURCES_FILE", sources), \
                 patch("youtube_monitor.STATE_FILE", state), \
                 patch("youtube_monitor.OUTPUT_FILE", output), \
                 patch("youtube_monitor.RUN_FILE", run_file), \
                 patch("youtube_monitor.api_get", side_effect=fake_api_get), \
                 patch.dict(os.environ, {"YOUTUBE_API_KEY": "test-api-key"}, clear=False):
                result = run_youtube_monitor()

            self.assertEqual(result, 0)
            run_result = json.loads(run_file.read_text(encoding="utf-8"))
            observation = json.loads(output.read_text(encoding="utf-8"))["observations"][0]
            saved_state = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(run_result["status"], "success")
            self.assertEqual(run_result["videos_checked"], 1)
            self.assertEqual(observation["event_type"], "game_announcement")
            self.assertEqual(observation["publisher_type"], "google_official_channel")
            self.assertEqual(saved_state["youtube_channels"]["google-cloud-tech"]["uploads_playlist_id"], "UU123456789")

    def test_youtube_monitor_is_disabled_without_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources = base / "sources.json"
            state = base / "state.json"
            output = base / "youtube.json"
            run_file = base / "last-youtube-run.json"
            sources.write_text(json.dumps({"youtube_channels": [{"channel_id": "UC12345678901234567890", "enabled": True}]}), encoding="utf-8")
            with patch("youtube_monitor.SOURCES_FILE", sources), \
                 patch("youtube_monitor.STATE_FILE", state), \
                 patch("youtube_monitor.OUTPUT_FILE", output), \
                 patch("youtube_monitor.RUN_FILE", run_file), \
                 patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}, clear=False), \
                 patch("youtube_monitor._API_OPENER.open") as api_open:
                result = run_youtube_monitor()
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(run_file.read_text(encoding="utf-8"))["status"], "disabled")
            api_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
