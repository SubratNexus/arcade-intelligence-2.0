import unittest

from agent.review_queue import build_review_queue, empty_approved_database, empty_review_queue


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.feed = {
            "programme_events": [],
            "skill_badges": [{
                "title": "Cloud Basics Skill Badge",
                "source_url": "https://www.skills.google/course_templates/1",
                "verified": True,
                "evidence": "Explicit SKILL BADGE label",
                "last_seen": "2026-11-27T00:00:00Z",
            }],
            "arcade_games": [],
            "completion_badges": [],
            "youtube_videos": [],
            "unresolved_candidates": [],
        }

    def test_first_run_bootstraps_existing_records_without_emailing_them(self):
        approved, queue = build_review_queue(self.feed, empty_approved_database(), empty_review_queue(), "2026-11-27T00:00:00Z")
        self.assertEqual(approved["initialization_status"], "initialized")
        self.assertEqual(len(approved["items"]), 1)
        self.assertEqual(queue["items"], [])

    def test_new_item_becomes_pending_candidate(self):
        approved, _ = build_review_queue(self.feed, empty_approved_database(), empty_review_queue(), "2026-11-27T00:00:00Z")
        changed = dict(self.feed)
        changed["arcade_games"] = [{
            "title": "Arcade Game: Build a Cloud App",
            "source_url": "https://www.skills.google/games/1",
            "verified": True,
        }]
        approved, queue = build_review_queue(changed, approved, empty_review_queue(), "2026-11-27T01:00:00Z")
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["status"], "pending")
        self.assertEqual(queue["items"][0]["change_type"], "add")

    def test_transient_last_seen_does_not_create_false_update(self):
        approved, _ = build_review_queue(self.feed, empty_approved_database(), empty_review_queue(), "2026-11-27T00:00:00Z")
        changed = {**self.feed, "skill_badges": [{**self.feed["skill_badges"][0], "last_seen": "2026-11-28T00:00:00Z"}]}
        _, queue = build_review_queue(changed, approved, empty_review_queue(), "2026-11-28T00:00:00Z")
        self.assertEqual(queue["items"], [])

    def test_material_edit_becomes_update(self):
        approved, _ = build_review_queue(self.feed, empty_approved_database(), empty_review_queue(), "2026-11-27T00:00:00Z")
        changed = {**self.feed, "skill_badges": [{**self.feed["skill_badges"][0], "evidence": "Updated visible badge label"}]}
        _, queue = build_review_queue(changed, approved, empty_review_queue(), "2026-11-28T00:00:00Z")
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["change_type"], "update")


    def test_first_bootstrap_does_not_auto_approve_unreviewed_programme_events(self):
        feed = {
            **self.feed,
            "programme_events": [{
                "event_id": "evt-1",
                "title": "New Arcade announcement",
                "verification_status": "officially_announced",
                "notification_approved": False,
                "review_decision": "pending",
                "source_urls": ["https://discuss.google.dev/t/example/1"],
            }],
        }
        approved, queue = build_review_queue(feed, empty_approved_database(), empty_review_queue(), "2026-11-27T00:00:00Z")
        self.assertEqual(len(approved["items"]), 1)  # Only the badge baseline.
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["entity_type"], "programme_event")
        self.assertEqual(queue["items"][0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
