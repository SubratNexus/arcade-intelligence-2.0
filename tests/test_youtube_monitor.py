import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from youtube_monitor import _safe_feed_url, parse_feed, run as run_youtube_monitor

CHANNEL_A = "UCJS9pqu9BzkAMNTmzNMNhvg"
CHANNEL_B = "UCTMRxtyHoE3LPcrl-kT4AQQ"
ATOM_FIXTURE = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <id>yt:video:abcdefghijk</id>
    <yt:videoId>abcdefghijk</yt:videoId>
    <title>Google Skills Arcade: New games and points</title>
    <published>2026-11-26T12:00:00+00:00</published>
    <media:group>
      <media:description>Google Skills Arcade programme update.</media:description>
      <media:thumbnail url="https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg" />
    </media:group>
  </entry>
</feed>'''


class YouTubeMonitorTests(unittest.TestCase):
    def test_feed_url_allowlist(self):
        self.assertTrue(_safe_feed_url(f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_A}", CHANNEL_A))
        self.assertFalse(_safe_feed_url(f"https://attacker.example/feeds/videos.xml?channel_id={CHANNEL_A}", CHANNEL_A))
        self.assertFalse(_safe_feed_url(f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_B}", CHANNEL_A))
        self.assertFalse(_safe_feed_url(f"https://www.youtube.com.evil.example/feeds/videos.xml?channel_id={CHANNEL_A}", CHANNEL_A))
        self.assertFalse(_safe_feed_url(f"http://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_A}", CHANNEL_A))

    def test_parse_feed_extracts_public_video_metadata(self):
        videos = parse_feed(ATOM_FIXTURE)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["video_id"], "abcdefghijk")
        self.assertIn("Google Skills Arcade", videos[0]["title"])
        self.assertIn("programme update", videos[0]["description"])
        self.assertTrue(videos[0]["thumbnail_url"].startswith("https://i.ytimg.com/"))

    def test_monitor_records_video_using_mocked_public_feed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources = base / "sources.json"
            state = base / "state.json"
            output = base / "youtube.json"
            run_file = base / "last-youtube-run.json"
            sources.write_text(json.dumps({
                "youtube_keywords": ["Google Skills Arcade"],
                "youtube_channels": [
                    {"id": "google-cloud-tech", "name": "Google Cloud Tech", "channel_id": CHANNEL_A, "enabled": True},
                ],
            }), encoding="utf-8")
            state.write_text('{"schema_version":1,"topics":{},"youtube_channels":{}}', encoding="utf-8")
            with patch("youtube_monitor.SOURCES_FILE", sources), \
                 patch("youtube_monitor.STATE_FILE", state), \
                 patch("youtube_monitor.OUTPUT_FILE", output), \
                 patch("youtube_monitor.RUN_FILE", run_file), \
                 patch("youtube_monitor.fetch_feed", return_value=ATOM_FIXTURE):
                result = run_youtube_monitor()
            self.assertEqual(result, 0)
            run_payload = json.loads(run_file.read_text(encoding="utf-8"))
            observations = json.loads(output.read_text(encoding="utf-8"))["observations"]
            self.assertEqual(run_payload["status"], "success")
            self.assertEqual(run_payload["videos_checked"], 1)
            self.assertEqual(observations[0]["publisher_type"], "google_official_channel")
            self.assertEqual(observations[0]["source_url"], "https://www.youtube.com/watch?v=abcdefghijk")
            self.assertEqual(observations[0]["matched_keywords"], ["Google Skills Arcade"])

    def test_feed_outage_preserves_existing_observations(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources = base / "sources.json"
            state = base / "state.json"
            output = base / "youtube.json"
            run_file = base / "last-youtube-run.json"
            existing = {"schema_version": 1, "updated_at": "2026-11-01T00:00:00Z", "observations": [{"observation_id": "youtube:oldvideo", "title": "Old"}]}
            sources.write_text(json.dumps({"youtube_channels": [
                {"id": "google-cloud-tech", "channel_id": CHANNEL_A, "enabled": True},
                {"id": "google-cloud", "channel_id": CHANNEL_B, "enabled": True},
            ]}), encoding="utf-8")
            state.write_text('{"schema_version":1,"topics":{},"youtube_channels":{}}', encoding="utf-8")
            output.write_text(json.dumps(existing), encoding="utf-8")
            with patch("youtube_monitor.SOURCES_FILE", sources), \
                 patch("youtube_monitor.STATE_FILE", state), \
                 patch("youtube_monitor.OUTPUT_FILE", output), \
                 patch("youtube_monitor.RUN_FILE", run_file), \
                 patch("youtube_monitor.fetch_feed", side_effect=RuntimeError("feed not available")):
                result = run_youtube_monitor()
            self.assertEqual(result, 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
            report = json.loads(run_file.read_text(encoding="utf-8"))
            self.assertEqual(saved, existing)
            self.assertEqual(report["status"], "unavailable")
            self.assertEqual(len(report["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
