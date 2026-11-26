import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from event_classifier import (
    classify_event,
    extract_explicit_point_mentions,
    extract_tier_mentions,
    html_to_text,
)
from program_watch import (
    _AllowlistedRedirectHandler,
    _topic_observation,
    _user_is_google_staff,
    extract_image_refs,
    run as run_program_watch,
    safe_url,
)
from evidence_engine import (
    _is_verified_google_staff_observation,
    _same_event,
    build_events,
)


class EventClassifierTests(unittest.TestCase):
    def test_official_swag_drop_title(self):
        self.assertEqual(classify_event("Swag Drop: The Arcade TechPack"), "swag_drop")

    def test_prize_counter_update_and_status(self):
        self.assertEqual(
            classify_event("Google Skills Arcade 2026: Prize Counter Update"),
            "prize_counter_update",
        )
        self.assertEqual(
            classify_event("Google Skills Arcade 2026: Prize Counter Status"),
            "prize_counter_update",
        )

    def test_buildathon_is_bonus_event(self):
        self.assertEqual(
            classify_event("Arcade Build-a-thon: Deploy Your AI Agent"),
            "bonus_event",
        )

    def test_facilitator_bonus_milestone_is_bonus_event(self):
        self.assertEqual(
            classify_event("Arcade Facilitator 2026 Bonus Milestone"),
            "bonus_event",
        )

    def test_points_policy(self):
        self.assertEqual(
            classify_event("Clarification: Arcade Points Calculation Changes"),
            "points_policy",
        )

    def test_tier_policy(self):
        self.assertEqual(classify_event("Google Skills Arcade 2026 Tiers"), "tier_policy")

    def test_game_announcement(self):
        self.assertEqual(
            classify_event("New Arcade Games for November 2026"),
            "game_announcement",
        )

    def test_program_address_update(self):
        self.assertEqual(
            classify_event("The Google Skills Arcade Has A New Address!"),
            "program_update",
        )

    def test_weekly_roundup(self):
        self.assertEqual(
            classify_event("InFocus This Week: Google Skills Arcade September 2026"),
            "programme_roundup",
        )

    def test_question_and_speculation_titles_are_not_events(self):
        self.assertIsNone(classify_event("Anyone know if the Prize Counter update is live?"))
        self.assertIsNone(classify_event("Is it true that Arcade swag is dropping today?"))
        self.assertIsNone(classify_event("Google Skills Arcade 2026: Prize Counter Update?"))

    def test_incidental_body_mention_does_not_change_type(self):
        self.assertIsNone(
            classify_event(
                "My Google Skills Arcade study plan",
                "I am Champion and I want to know about the backpack and Prize Counter.",
            )
        )

    def test_known_programme_announcement_title_examples(self):
        examples = {
            "Swag Drop: The Arcade Polo T-Shirt": "swag_drop",
            "Arcade Build-a-thon: Deploy Your AI Agent": "bonus_event",
            "Swag Drop: The Arcade TechPack": "swag_drop",
            "Swag Drop: The Arcade Vacuum Tumbler": "swag_drop",
            "The Google Skills Arcade Has A New Address!": "program_update",
            "Swag Drop: The Arcade Magic Mug": "swag_drop",
            "Swag Drop: The Arcade LumiSnap Magnetic Fill Light": "swag_drop",
            "Swag Drop: The Arcade Weather-Shield Jacket": "swag_drop",
            "Swag Drop: The Arcade Travel Bottle": "swag_drop",
            "Swag Drop: The Arcade PopUp Pro": "swag_drop",
            "InFocus This Week: Google Skills Arcade September 2026": "programme_roundup",
            "Arcade Facilitator 2026 Bonus Milestone": "bonus_event",
            "Arcade Re-Trail": "program_update",
            "Google Skills Arcade 2026: Prize Counter Update": "prize_counter_update",
            "Google Skills Arcade 2026 Tiers": "tier_policy",
            "Google Skills Arcade Eligibility Requirements Update": "tier_policy",
            "Google Skills Arcade: New Game Badges": "game_announcement",
            "Google Skills Arcade: Special Announcement": "program_update",
            "Google Skills Arcade Programme Rules Update": "program_update",
        }
        for title, expected in examples.items():
            with self.subTest(title=title):
                self.assertEqual(classify_event(title), expected)

    def test_generic_programme_mention_is_not_an_event(self):
        self.assertIsNone(classify_event("Google Skills Arcade 2026"))
        self.assertIsNone(classify_event("My Cloud certification study plan"))
        self.assertIsNone(classify_event("Leaving 10 Bonus Arcade Points Behind? Let's Fix That Today"))

    def test_point_mentions_are_explicit_and_deduplicated(self):
        mentions = extract_explicit_point_mentions(
            "Earn 6 Arcade Points plus 1 bonus point; later 6 arcade points."
        )
        self.assertEqual(len(mentions), 2)
        self.assertIn("6 Arcade Points", mentions)

    def test_tier_mentions_are_case_insensitive(self):
        self.assertEqual(extract_tier_mentions("available to trooper and RANGER"), ["Trooper", "Ranger"])

    def test_html_to_text_removes_tags_and_script_content(self):
        self.assertEqual(
            html_to_text("<p>Hello <b>Arcade</b></p><script>secret()</script>"),
            "Hello Arcade",
        )


class SourceSafetyTests(unittest.TestCase):
    def test_forum_url_allowlist_and_port_validation(self):
        self.assertTrue(safe_url("https://discuss.google.dev/c/example/172.json"))
        self.assertFalse(safe_url("http://discuss.google.dev/c/example/172.json"))
        self.assertFalse(safe_url("https://example.com/c/example/172.json"))
        self.assertFalse(safe_url("https://user@discuss.google.dev/c/example/172.json"))
        self.assertFalse(safe_url("https://discuss.google.dev:444/c/example/172.json"))

    def test_redirect_to_unapproved_host_is_blocked_before_follow(self):
        request = urllib.request.Request("https://discuss.google.dev/source.json")
        handler = _AllowlistedRedirectHandler()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/collect")

    def test_redirect_within_allowlisted_host_is_allowed(self):
        request = urllib.request.Request("https://discuss.google.dev/source.json")
        handler = _AllowlistedRedirectHandler()
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://discuss.google.dev/source-v2.json"
        )
        self.assertEqual(redirected.full_url, "https://discuss.google.dev/source-v2.json")

    def test_forum_privilege_flags_do_not_prove_google_employment(self):
        self.assertFalse(_user_is_google_staff("ordinary", 6, [], {"staff": True}))
        self.assertFalse(_user_is_google_staff("ordinary", 6, [], {"admin": True}))
        self.assertFalse(_user_is_google_staff("ordinary", 6, [], {"moderator": True}))

    def test_exact_google_staff_group_metadata_is_accepted(self):
        users = [{"id": 5, "username": "example", "primary_group_name": "google_staff"}]
        self.assertTrue(_user_is_google_staff("example", 5, users, {}))
        users = [{"id": 5, "username": "example", "groups": [{"name": "google_staff"}]}]
        self.assertTrue(_user_is_google_staff("example", 5, users, {}))
        users = [{"id": 5, "username": "example", "flair_name": "google_staff"}]
        self.assertFalse(_user_is_google_staff("example", 5, users, {}))
        # A matching username cannot override a different authoritative user ID.
        self.assertFalse(_user_is_google_staff("example", 6, users, {"staff": True}))

    def test_topic_observation_records_verification_method(self):
        topic = {
            "id": 99,
            "slug": "swag-drop-arcade-techpack",
            "title": "Swag Drop: The Arcade TechPack",
            "reply_count": 1,
            "created_at": "2026-11-08T00:00:00Z",
        }
        detail = {
            "title": topic["title"],
            "post_stream": {
                "posts": [{
                    "post_number": 1,
                    "user_id": 7,
                    "username": "staff_author",
                    "staff": True,
                    "cooked": '<p>Available to Champion and Legend tiers.</p><img src="https://cdn.example.org/techpack.png" alt="Arcade TechPack">',
                    "created_at": topic["created_at"],
                }]
            },
        }
        users = [{"id": 7, "username": "staff_author", "primary_group_name": "google_staff"}]
        observation = _topic_observation(
            topic, detail, users, {"name": "Google Cloud Community"}, topic["created_at"]
        )
        self.assertTrue(observation["is_google_staff_author"])
        self.assertEqual(observation["publisher_authority"], "google_staff_group")
        self.assertEqual(observation["author_verification_method"], "discourse_google_staff_group")
        self.assertEqual(observation["event_type"], "swag_drop")
        self.assertEqual(observation["image_references"][0]["alt"], "Arcade TechPack")

    def test_image_references_require_https_and_no_credentials(self):
        images = extract_image_refs(
            '<img src="https://cdn.example.org/x.png">'
            '<img src="http://cdn.example.org/y.png">'
            '<img src="https://user@cdn.example.org/z.png">'
            '<img src="javascript:alert(1)">',
            "https://discuss.google.dev/t/demo/1",
        )
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["url"], "https://cdn.example.org/x.png")

    def test_program_watcher_processes_public_source_fixture_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources_file = base / "sources.json"
            state_file = base / "watcher-state.json"
            output_file = base / "community-observations.json"
            run_file = base / "last-program-watch.json"
            source_url = "https://discuss.google.dev/c/google-cloud/cloud-announcements/172.json"
            topic = {
                "id": 731,
                "slug": "swag-drop-arcade-techpack",
                "title": "Swag Drop: The Arcade TechPack",
                "excerpt": "Official Arcade swag announcement",
                "reply_count": 1,
                "created_at": "2026-11-01T00:00:00Z",
                "last_posted_at": "2026-11-01T00:10:00Z",
            }
            detail = {
                "id": 731,
                "slug": topic["slug"],
                "title": topic["title"],
                "post_stream": {"posts": [{
                    "post_number": 1,
                    "user_id": 7,
                    "username": "google_author",
                    "created_at": topic["created_at"],
                    "cooked": "<p>Arcade TechPack is available.</p>",
                }]},
            }
            sources_file.write_text(json.dumps({"discourse_categories": [{
                "id": "announcements", "name": "Announcements", "url": source_url, "enabled": True,
            }]}), encoding="utf-8")
            state_file.write_text('{"schema_version": 1, "topics": {}, "youtube_channels": {}}', encoding="utf-8")
            output_file.write_text('{"schema_version": 1, "updated_at": null, "observations": []}', encoding="utf-8")

            class FakeRobotsGate:
                def get_json(self, url):
                    if url == source_url:
                        return {"topic_list": {"topics": [topic]}, "users": [{
                            "id": 7, "username": "google_author", "primary_group_name": "google_staff",
                        }]}
                    if url.endswith("/731.json"):
                        return detail
                    raise AssertionError(f"Unexpected fixture URL: {url}")

            with patch("program_watch.SOURCES_FILE", sources_file), \
                 patch("program_watch.STATE_FILE", state_file), \
                 patch("program_watch.OUTPUT_FILE", output_file), \
                 patch("program_watch.RUN_FILE", run_file), \
                 patch("program_watch.RobotsGate", FakeRobotsGate):
                result = run_program_watch()

            self.assertEqual(result, 0)
            run_result = json.loads(run_file.read_text(encoding="utf-8"))
            observation = json.loads(output_file.read_text(encoding="utf-8"))["observations"][0]
            saved_state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(run_result["status"], "success")
            self.assertIn("731", saved_state["topics"])
            self.assertEqual(observation["event_type"], "swag_drop")
            self.assertTrue(observation["is_google_staff_author"])
            self.assertEqual(observation["author_verification_method"], "discourse_google_staff_group")

    def test_program_watcher_returns_failure_when_source_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources_file = base / "sources.json"
            state_file = base / "watcher-state.json"
            output_file = base / "community-observations.json"
            run_file = base / "last-program-watch.json"
            sources_file.write_text(json.dumps({"discourse_categories": [{
                "id": "announcements",
                "url": "https://discuss.google.dev/c/google-cloud/cloud-announcements/172.json",
                "enabled": True,
            }]}), encoding="utf-8")

            class BrokenRobotsGate:
                def get_json(self, _url):
                    raise OSError("fixture network failure")

            with patch("program_watch.SOURCES_FILE", sources_file), \
                 patch("program_watch.STATE_FILE", state_file), \
                 patch("program_watch.OUTPUT_FILE", output_file), \
                 patch("program_watch.RUN_FILE", run_file), \
                 patch("program_watch.RobotsGate", BrokenRobotsGate):
                result = run_program_watch()

            self.assertEqual(result, 1)
            run_result = json.loads(run_file.read_text(encoding="utf-8"))
            self.assertEqual(run_result["status"], "failed")
            self.assertTrue(run_result["errors"])


class EvidenceTests(unittest.TestCase):
    def test_staff_flag_without_group_evidence_is_not_trusted(self):
        old_style = {
            "source_type": "google_cloud_community",
            "is_google_staff_author": True,
            "publisher_authority": "google_staff",
        }
        self.assertFalse(_is_verified_google_staff_observation(old_style))

    def test_only_exact_group_marker_is_trusted(self):
        trusted = {
            "source_type": "google_cloud_community",
            "source_domain": "discuss.google.dev",
            "source_url": "https://discuss.google.dev/t/example/123",
            "is_google_staff_author": True,
            "publisher_authority": "google_staff_group",
            "author_verification_method": "discourse_google_staff_group",
        }
        self.assertTrue(_is_verified_google_staff_observation(trusted))

    def test_different_swag_products_do_not_merge(self):
        a = {"event_type": "swag_drop", "title": "Swag Drop: Arcade TechPack"}
        b = {"event_type": "swag_drop", "title": "Swag Drop: Arcade Vacuum Tumbler"}
        self.assertFalse(_same_event(a, b))

    def test_same_product_title_variants_can_merge(self):
        a = {"event_type": "swag_drop", "title": "Swag Drop: The Arcade TechPack"}
        b = {"event_type": "swag_drop", "title": "Arcade TechPack prize counter update"}
        self.assertTrue(_same_event(a, b))

    def test_evidence_engine_requires_exact_group_marker_for_official_status(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            community_file = base / "community.json"
            youtube_file = base / "youtube.json"
            events_file = base / "events.json"
            community_file.write_text(json.dumps({"observations": [{
                "observation_id": "discourse:1",
                "source_type": "google_cloud_community",
                "source_name": "Google Cloud Community",
                "source_domain": "discuss.google.dev",
                "source_url": "https://discuss.google.dev/t/swag-drop/1",
                "title": "Swag Drop: Arcade TechPack",
                "event_type": "swag_drop",
                "is_google_staff_author": True,
                "publisher_authority": "google_staff",
                "published_at": "2026-11-01T00:00:00Z",
                "text_excerpt": "Available to Champion and Legend tiers.",
            }]}), encoding="utf-8")
            youtube_file.write_text('{"observations": []}', encoding="utf-8")
            with patch("evidence_engine.COMMUNITY_FILE", community_file), \
                 patch("evidence_engine.YOUTUBE_FILE", youtube_file), \
                 patch("evidence_engine.EVENTS_FILE", events_file):
                result = build_events()
            event = result["events"][0]
            self.assertEqual(event["verification_status"], "community_report_unverified")
            self.assertTrue(event["human_review_required"])
            self.assertFalse(event["notification_approved"])

    def test_manual_approval_is_persisted_and_bound_to_current_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            community_file = base / "community.json"
            youtube_file = base / "youtube.json"
            events_file = base / "events.json"
            decisions_file = base / "review-decisions.json"
            community_file.write_text(json.dumps({"observations": [{
                "observation_id": "discourse:42",
                "source_type": "google_cloud_community",
                "source_name": "Google Cloud Community",
                "source_domain": "discuss.google.dev",
                "source_url": "https://discuss.google.dev/t/swag-drop-techpack/42",
                "title": "Swag Drop: Arcade TechPack",
                "event_type": "swag_drop",
                "is_google_staff_author": True,
                "publisher_authority": "google_staff_group",
                "author_verification_method": "discourse_google_staff_group",
                "published_at": "2026-11-01T00:00:00Z",
                "last_seen_at": "2026-11-01T00:00:00Z",
                "text_excerpt": "Available to Champion and Legend tiers.",
            }]}), encoding="utf-8")
            youtube_file.write_text('{"observations": []}', encoding="utf-8")
            decisions_file.write_text('{"schema_version": 1, "decisions": []}', encoding="utf-8")
            with patch("evidence_engine.COMMUNITY_FILE", community_file), \
                 patch("evidence_engine.YOUTUBE_FILE", youtube_file), \
                 patch("evidence_engine.EVENTS_FILE", events_file), \
                 patch("evidence_engine.REVIEW_DECISIONS_FILE", decisions_file):
                pending = build_events()["events"][0]
                self.assertTrue(pending["human_review_required"])
                self.assertFalse(pending["notification_approved"])
                decision = {
                    "event_id": pending["event_id"],
                    "approval_fingerprint": pending["approval_fingerprint"],
                    "decision": "approve",
                    "reviewed_at": "2026-11-01T01:00:00Z",
                    "reviewed_source_url": pending["source_urls"][0],
                }
                decisions_file.write_text(json.dumps({"schema_version": 1, "decisions": [decision]}), encoding="utf-8")
                approved = build_events()["events"][0]
                self.assertFalse(approved["human_review_required"])
                self.assertTrue(approved["notification_approved"])
                self.assertEqual(approved["review_decision"], "approve")

                # A changed source body produces a different fingerprint and cancels approval.
                changed = json.loads(community_file.read_text(encoding="utf-8"))
                changed["observations"][0]["text_excerpt"] += " Updated availability details."
                community_file.write_text(json.dumps(changed), encoding="utf-8")
                stale = build_events()["events"][0]
                self.assertTrue(stale["human_review_required"])
                self.assertFalse(stale["notification_approved"])
                self.assertEqual(stale["review_decision"], "pending")

    def test_official_youtube_video_alone_is_not_programme_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            community_file = base / "community.json"
            youtube_file = base / "youtube.json"
            events_file = base / "events.json"
            community_file.write_text('{"observations": []}', encoding="utf-8")
            youtube_file.write_text(json.dumps({"observations": [{
                "observation_id": "youtube:video1",
                "source_type": "youtube_video",
                "source_name": "Google Cloud Tech",
                "source_domain": "youtube.com",
                "source_url": "https://www.youtube.com/watch?v=video1",
                "publisher_type": "google_official_channel",
                "title": "Arcade points update",
                "event_type": "points_policy",
                "published_at": "2026-11-03T00:00:00Z",
                "text_excerpt": "Google Skills Arcade points update.",
            }]}), encoding="utf-8")
            with patch("evidence_engine.COMMUNITY_FILE", community_file), \
                 patch("evidence_engine.YOUTUBE_FILE", youtube_file), \
                 patch("evidence_engine.EVENTS_FILE", events_file):
                result = build_events()
            self.assertEqual(
                result["events"][0]["verification_status"],
                "official_channel_coverage_unconfirmed",
            )


if __name__ == "__main__":
    unittest.main()
