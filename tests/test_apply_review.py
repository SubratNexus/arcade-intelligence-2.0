import unittest

from agent.apply_review import apply_decision
from agent.review_queue import empty_approved_database


class ApplyReviewTests(unittest.TestCase):
    def setUp(self):
        self.approved = empty_approved_database()
        self.approved.update({"initialization_status": "initialized", "items": []})
        self.queue = {"items": [{
            "item_id": "review-001",
            "entity_type": "skill_badge",
            "entity_id": "skill_badge:abc",
            "change_type": "add",
            "title": "Example Skill Badge",
            "source_url": "https://www.skills.google/example",
            "record": {"title": "Example Skill Badge", "source_url": "https://www.skills.google/example"},
            "digest": "digest-001",
            "status": "pending",
        }]}

    def test_approve_adds_only_that_record_to_approved_database(self):
        approved, queue = apply_decision("review-001", "approve", self.approved, self.queue, "2026-11-27T00:00:00Z")
        self.assertEqual(len(approved["items"]), 1)
        self.assertEqual(approved["items"][0]["entity_id"], "skill_badge:abc")
        self.assertEqual(queue["items"][0]["status"], "approved")

    def test_reject_does_not_change_approved_database(self):
        approved, queue = apply_decision("review-001", "reject", self.approved, self.queue, "2026-11-27T00:00:00Z")
        self.assertEqual(approved["items"], [])
        self.assertEqual(queue["items"][0]["status"], "rejected")

    def test_same_decision_is_idempotent(self):
        approved, queue = apply_decision("review-001", "approve", self.approved, self.queue, "2026-11-27T00:00:00Z")
        again_approved, again_queue = apply_decision("review-001", "approve", approved, queue, "2026-11-27T01:00:00Z")
        self.assertEqual(len(again_approved["items"]), 1)
        self.assertEqual(again_queue["items"][0]["reviewed_at"], "2026-11-27T00:00:00Z")

    def test_opposite_second_decision_is_rejected(self):
        approved, queue = apply_decision("review-001", "approve", self.approved, self.queue, "2026-11-27T00:00:00Z")
        with self.assertRaises(ValueError):
            apply_decision("review-001", "reject", approved, queue)


if __name__ == "__main__":
    unittest.main()
