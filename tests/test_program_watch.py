import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from event_classifier import classify_event, extract_explicit_point_mentions, extract_tier_mentions, html_to_text
from program_watch import _topic_observation, _user_is_google_staff, extract_image_refs, safe_url
from evidence_engine import _same_event, build_events


class EventClassifierTests(unittest.TestCase):
    def test_swag_drop_title(self):
        self.assertEqual(classify_event("Swag Drop: The Arcade TechPack"), "swag_drop")

    def test_bonus_buildathon(self):
        self.assertEqual(classify_event("Arcade Build-a-thon: Deploy Your AI Agent"), "bonus_event")

    def test_points_policy(self):
        self.assertEqual(classify_event("Clarification: Arcade Points Calculation Changes"), "points_policy")

    def test_game_announcement(self):
        self.assertEqual(classify_event("New Arcade Games for November 2026"), "game_announcement")

    def test_unrelated_topic_stays_unclassified(self):
        self.assertIsNone(classify_event("My Cloud certification study plan", "I am studying for an exam."))

    def test_generic_programme_mention_is_not_automatically_an_update(self):
        self.assertIsNone(classify_event("Google Skills Arcade 2026", "I am participating this year."))

    def test_new_arcade_address_is_a_programme_update(self):
        self.assertEqual(classify_event("The Google Skills Arcade Has A New Address!"), "program_update")

    def test_extract_explicit_point_mentions_without_calculating(self):
        mentions = extract_explicit_point_mentions("Earn 6 Arcade Points plus 1 bonus point for sharing.")
        self.assertEqual(len(mentions), 2)
        self.assertIn("6 Arcade Points", mentions[0])

    def test_extract_tier_mentions(self):
        self.assertEqual(extract_tier_mentions("Available to Trooper and Ranger tiers"), ["Trooper", "Ranger"])

    def test_html_to_text_removes_tags(self):
        self.assertEqual(html_to_text("<p>Hello <b>Arcade</b></p>"), "Hello Arcade")


class SourceSafetyTests(unittest.TestCase):
    def test_only_https_allowlisted_forum_host(self):
        self.assertTrue(safe_url("https://discuss.google.dev/c/example/172.json"))
        self.assertFalse(safe_url("http://discuss.google.dev/c/example/172.json"))
        self.assertFalse(safe_url("https://example.com/c/example/172.json"))
        self.assertFalse(safe_url("https://user@discuss.google.dev/c/example/172.json"))

    def test_staff_detection_requires_google_staff_metadata_or_staff_flag(self):
        users = [{"id": 5, "username": "example", "primary_group_name": "google_staff"}]
        self.assertTrue(_user_is_google_staff("example", 5, users, {}))
        self.assertFalse(_user_is_google_staff("ordinary", 6, users, {}))
        self.assertTrue(_user_is_google_staff("ordinary", 6, [], {"staff": True}))

    def test_topic_observation_extracts_staff_author_and_image_reference(self):
        topic = {"id": 99, "slug": "swag-drop-arcade-techpack", "title": "Swag Drop: Arcade TechPack", "reply_count": 2, "created_at": "2026-11-08T00:00:00Z", "last_posted_at": "2026-11-08T02:00:00Z"}
        detail = {"title": topic["title"], "post_stream": {"posts": [
            {"post_number": 1, "user_id": 7, "username": "staff_author", "cooked": '<p>Available exclusively at Champion and Legend tiers.</p><img src="https://cdn.example.org/techpack.png" alt="Arcade TechPack">', "created_at": topic["created_at"]},
            {"post_number": 2, "username": "member1", "cooked": "I can see it in the Prize Counter.", "created_at": topic["last_posted_at"]}
        ]}}
        users = [{"id": 7, "username": "staff_author", "primary_group_name": "google_staff"}]
        observation = _topic_observation(topic, detail, users, {"name": "Google Cloud Community"}, "2026-11-08T00:00:00Z")
        self.assertTrue(observation["is_google_staff_author"])
        self.assertEqual(observation["event_type"], "swag_drop")
        self.assertEqual(observation["reply_count"], 2)
        self.assertEqual(observation["image_references"][0]["alt"], "Arcade TechPack")
        self.assertEqual(observation["discussion_signals"]["positive_report_phrase_hits"], 1)

    def test_image_references_are_https_only(self):
        images = extract_image_refs('<img src="https://cdn.example.org/x.png"><img src="javascript:alert(1)">', "https://discuss.google.dev/t/demo/1")
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["url"].startswith("https://"))


class EvidenceTests(unittest.TestCase):
    def test_different_swag_products_do_not_merge(self):
        a = {"event_type": "swag_drop", "title": "Swag Drop: Arcade TechPack"}
        b = {"event_type": "swag_drop", "title": "Swag Drop: Arcade Vacuum Tumbler"}
        self.assertFalse(_same_event(a, b))

    def test_same_product_from_different_title_forms_can_merge(self):
        a = {"event_type": "swag_drop", "title": "Swag Drop: The Arcade TechPack"}
        b = {"event_type": "swag_drop", "title": "Arcade TechPack prize counter update"}
        self.assertTrue(_same_event(a, b))

    def test_staff_announcement_is_only_official_status(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            community_file = base / "community.json"
            youtube_file = base / "youtube.json"
            events_file = base / "events.json"
            community_file.write_text(json.dumps({"observations": [{
                "observation_id": "discourse:1", "source_type": "google_cloud_community",
                "source_name": "Google Cloud Community", "source_domain": "discuss.google.dev",
                "source_url": "https://discuss.google.dev/t/swag-drop/1", "title": "Swag Drop: Arcade TechPack",
                "event_type": "swag_drop", "is_google_staff_author": True,
                "published_at": "2026-11-01T00:00:00Z", "last_seen_at": "2026-11-01T00:00:00Z",
                "text_excerpt": "Available to Champion and Legend tiers."
            }]}), encoding="utf-8")
            youtube_file.write_text(json.dumps({"observations": []}), encoding="utf-8")
            with patch("evidence_engine.COMMUNITY_FILE", community_file), patch("evidence_engine.YOUTUBE_FILE", youtube_file), patch("evidence_engine.EVENTS_FILE", events_file):
                result = build_events()
            self.assertEqual(result["events"][0]["verification_status"], "officially_announced")
            self.assertTrue(result["events"][0]["human_review_required"])

    def test_official_google_video_alone_is_not_programme_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            community_file = base / "community.json"
            youtube_file = base / "youtube.json"
            events_file = base / "events.json"
            community_file.write_text('{"observations": []}', encoding="utf-8")
            youtube_file.write_text(json.dumps({"observations": [{
                "observation_id": "youtube:video1", "source_type": "youtube_video",
                "source_name": "Google Cloud Tech", "source_domain": "youtube.com",
                "source_url": "https://www.youtube.com/watch?v=video1",
                "publisher_type": "google_official_channel", "title": "Arcade points update",
                "event_type": "points_policy", "published_at": "2026-11-03T00:00:00Z",
                "last_seen_at": "2026-11-03T00:00:00Z", "text_excerpt": "Google Skills Arcade points update."
            }]}), encoding="utf-8")
            with patch("evidence_engine.COMMUNITY_FILE", community_file), patch("evidence_engine.YOUTUBE_FILE", youtube_file), patch("evidence_engine.EVENTS_FILE", events_file):
                result = build_events()
            self.assertEqual(result["events"][0]["verification_status"], "official_channel_coverage_unconfirmed")

    def test_community_member_is_not_official(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            community_file = base / "community.json"
            youtube_file = base / "youtube.json"
            events_file = base / "events.json"
            community_file.write_text(json.dumps({"observations": [{
                "observation_id": "discourse:2", "source_type": "google_cloud_community",
                "source_domain": "discuss.google.dev", "source_url": "https://discuss.google.dev/t/topic/2",
                "title": "Swag Drop: Arcade Polo T-Shirt", "event_type": "swag_drop",
                "is_google_staff_author": False, "published_at": "2026-11-02T00:00:00Z",
                "last_seen_at": "2026-11-02T00:00:00Z", "text_excerpt": "I think this is coming soon."
            }]}), encoding="utf-8")
            youtube_file.write_text(json.dumps({"observations": []}), encoding="utf-8")
            with patch("evidence_engine.COMMUNITY_FILE", community_file), patch("evidence_engine.YOUTUBE_FILE", youtube_file), patch("evidence_engine.EVENTS_FILE", events_file):
                result = build_events()
            self.assertEqual(result["events"][0]["verification_status"], "community_report_unverified")


if __name__ == "__main__":
    unittest.main()
