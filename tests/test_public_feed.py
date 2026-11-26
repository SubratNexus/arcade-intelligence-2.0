import sys
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from public_feed import build_public_feed


class PublicFeedTests(unittest.TestCase):
    def test_feed_exports_only_allowlisted_public_fields(self):
        events = {"events": [{
            "event_id": "evt-1",
            "title": "Arcade bonus event",
            "event_type": "bonus_event",
            "verification_status": "officially_announced",
            "evidence_score": 0.95,
            "verification_explanation": "Verified source.",
            "published_at": "2026-11-26T00:00:00Z",
            "last_observed_at": "2026-11-26T01:00:00Z",
            "source_urls": ["https://discuss.google.dev/t/arcade/123", "https://evil.example/fake"],
            "sources": [{
                "source_type": "google_cloud_community",
                "source_name": "Google Cloud Community",
                "source_domain": "discuss.google.dev",
                "source_url": "https://discuss.google.dev/t/arcade/123",
                "publisher_authority": "google_staff_group",
                "is_google_staff_author": True,
                "author_verification_method": "discourse_google_staff_group",
                "private_email": "not-for-export@example.test",
            }],
            "explicit_point_mentions": ["+2 points"],
            "tier_mentions": ["Ranger"],
            "human_review_required": True,
            "notification_approved": False,
            "review_decision": "pending",
            "approval_fingerprint": "must-not-export",
            "observation_ids": ["must-not-export"],
        }]}
        feed = build_public_feed(events, {"observations": []}, "2026-11-27T00:00:00Z")
        event = feed["programme_events"][0]
        self.assertEqual(event["source_urls"], ["https://discuss.google.dev/t/arcade/123"])
        self.assertNotIn("approval_fingerprint", event)
        self.assertNotIn("observation_ids", event)
        self.assertNotIn("private_email", str(feed))
        self.assertTrue(event["human_review_required"])
        self.assertFalse(event["notification_approved"])

    def test_youtube_export_requires_youtube_url(self):
        feed = build_public_feed({}, {"observations": [
            {"title": "Good video", "source_url": "https://www.youtube.com/watch?v=abcdefghijk", "thumbnail_url": "https://i.ytimg.com/vi/abcdefghijk/default.jpg"},
            {"title": "Bad link", "source_url": "https://attacker.example/watch?v=bad"},
        ]}, "2026-11-27T00:00:00Z")
        self.assertEqual(len(feed["youtube_videos"]), 1)
        self.assertEqual(feed["youtube_videos"][0]["source_url"], "https://www.youtube.com/watch?v=abcdefghijk")

    def test_empty_and_invalid_input_produces_valid_empty_feed(self):
        feed = build_public_feed(None, None, "2026-11-27T00:00:00Z")
        self.assertEqual(feed["programme_events"], [])
        self.assertEqual(feed["youtube_videos"], [])
        self.assertEqual(feed["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
