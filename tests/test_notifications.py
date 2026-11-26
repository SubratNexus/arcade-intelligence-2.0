import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from notification_manager import send_pending_alerts
from youtube_monitor import run as run_youtube_monitor


class NotificationTests(unittest.TestCase):
    def test_youtube_monitor_is_safely_disabled_without_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources = base / "sources.json"
            state = base / "state.json"
            output = base / "youtube.json"
            run_file = base / "last-run.json"
            sources.write_text('{"youtube_channels": []}', encoding="utf-8")
            with patch("youtube_monitor.SOURCES_FILE", sources), patch("youtube_monitor.STATE_FILE", state), patch("youtube_monitor.OUTPUT_FILE", output), patch("youtube_monitor.RUN_FILE", run_file):
                with patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}, clear=False):
                    result = run_youtube_monitor()
            self.assertEqual(result, 0)
            self.assertEqual(__import__("json").loads(run_file.read_text(encoding="utf-8"))["status"], "disabled")

    def test_notifications_are_disabled_without_all_smtp_settings(self):
        names = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"]
        with tempfile.TemporaryDirectory() as temp:
            events_file = Path(temp) / "events.json"
            state_file = Path(temp) / "state.json"
            events_file.write_text('{"events": []}', encoding="utf-8")
            with patch("notification_manager.EVENTS_FILE", events_file), patch("notification_manager.STATE_FILE", state_file):
                with patch.dict(os.environ, {name: "" for name in names}, clear=False):
                    result = send_pending_alerts()
            self.assertEqual(result["status"], "disabled")
            self.assertFalse(state_file.exists())


if __name__ == "__main__":
    unittest.main()
