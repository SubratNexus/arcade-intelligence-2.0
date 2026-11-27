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

    def test_verified_catalogue_sections_are_exported_separately(self):
        catalogue = {
            "version": "1.2.0",
            "last_run_at": "2026-11-27T00:00:00Z",
            "status": "ok",
            "entries": [
                {"title": "Intro to Cloud Fundamentals", "verified": True, "category": "skill", "evidence": "Explicit visible SKILL BADGE label", "source_url": "https://www.skills.google/paths/1", "badge_url": "https://www.skills.google/paths/1", "image_url": "https://cdn.qwiklabs.com/badge.png"},
                {"title": "Google Skills", "verified": True, "source_url": "https://www.skills.google"},
                {"title": "Rumour Badge", "verified": False, "source_url": "https://www.skills.google/badges/rumour"},
            ],
            "game_entries": [{"title": "Arcade Game Alpha", "verified": True, "source_url": "https://www.skills.google/games/alpha"}],
            "completion_badges": [{"title": "Learning Path Completion", "verified": True, "source_url": "https://www.skills.google/completions/1"}],
            "candidates": [{"title": "Possible Badge", "source_url": "https://www.skills.google/badges/candidate", "confidence": 0.4}],
        }
        feed = build_public_feed({}, {}, "2026-11-27T00:00:00Z", catalogue)
        self.assertEqual([x["title"] for x in feed["skill_badges"]], ["Intro to Cloud Fundamentals"])
        self.assertEqual(len(feed["arcade_games"]), 1)
        self.assertEqual(len(feed["completion_badges"]), 1)
        self.assertEqual(feed["unresolved_candidates"][0]["review_status"], "pending_review")
        self.assertIsNone(feed["skill_badges"][0]["point_value"])
        self.assertFalse(feed["skill_badges"][0]["availability_confirmed"])

    def test_untrusted_catalogue_urls_are_rejected(self):
        catalogue = {"entries": [{
            "title": "Unsafe fake badge", "verified": True,
            "source_url": "https://attacker.example/badge", "badge_url": "https://attacker.example/badge"
        }]}
        feed = build_public_feed({}, {}, "2026-11-27T00:00:00Z", catalogue)
        self.assertEqual(feed["skill_badges"], [])

    def test_change_history_records_addition_and_material_update(self):
        first_catalogue = {"entries": [{
            "title": "Intro to Cloud Fundamentals", "verified": True,
            "evidence": "Explicit SKILL BADGE label", "source_url": "https://www.skills.google/badges/intro"
        }]}
        first = build_public_feed({}, {}, "2026-11-27T00:00:00Z", first_catalogue)
        self.assertEqual(first["change_history"], [])
        second_catalogue = {"entries": [{
            "title": "Intro to Cloud Fundamentals", "verified": True,
            "evidence": "Explicit SKILL BADGE label updated", "source_url": "https://www.skills.google/badges/intro"
        }, {
            "title": "Cloud Security Basics", "verified": True,
            "evidence": "Explicit SKILL BADGE label", "source_url": "https://www.skills.google/badges/security"
        }]}
        second = build_public_feed({}, {}, "2026-11-27T00:30:00Z", second_catalogue, first)
        self.assertEqual({x["change_type"] for x in second["change_history"]}, {"added", "updated"})
        self.assertEqual(second["schema_version"], 1)
        self.assertTrue(second["knowledge_version"])

    def test_polling_timestamp_does_not_create_false_knowledge_change(self):
        event = {"events": [{"event_id": "evt-1", "title": "Arcade update", "last_observed_at": "2026-11-27T00:00:00Z"}]}
        first = build_public_feed(event, {}, "2026-11-27T00:00:00Z")
        event["events"][0]["last_observed_at"] = "2026-11-27T00:30:00Z"
        second = build_public_feed(event, {}, "2026-11-27T00:30:00Z", previous_data=first)
        self.assertEqual(first["knowledge_version"], second["knowledge_version"])
        self.assertEqual(second["change_history"], [])

    def test_catalogue_last_seen_timestamp_does_not_change_knowledge_version(self):
        catalogue = {"entries": [{
            "title": "Intro to Cloud Fundamentals", "verified": True,
            "source_url": "https://www.skills.google/badges/intro",
            "last_seen": "2026-11-27T00:00:00Z"
        }]}
        first = build_public_feed({}, {}, "2026-11-27T00:00:00Z", catalogue)
        catalogue["entries"][0]["last_seen"] = "2026-11-27T00:30:00Z"
        second = build_public_feed({}, {}, "2026-11-27T00:30:00Z", catalogue, first)
        self.assertEqual(first["knowledge_version"], second["knowledge_version"])
        self.assertEqual(second["change_history"], [])

    def test_empty_and_invalid_input_produces_valid_empty_feed(self):
        feed = build_public_feed(None, None, "2026-11-27T00:00:00Z")
        self.assertEqual(feed["programme_events"], [])
        self.assertEqual(feed["youtube_videos"], [])
        self.assertEqual(feed["schema_version"], 1)
        self.assertEqual(feed["skill_badges"], [])


if __name__ == "__main__":
    unittest.main()
