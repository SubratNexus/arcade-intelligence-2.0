import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from agent.review_digest import build_email, review_signature, review_url, send_digest


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"id":"email_test"}'


class ReviewDigestTests(unittest.TestCase):
    def setUp(self):
        self.queue = {"items": [{
            "item_id": "abc123",
            "entity_type": "skill_badge",
            "change_type": "add",
            "title": "New Skill Badge",
            "source_url": "https://www.skills.google/course_templates/2",
            "record": {"title": "New Skill Badge", "source_url": "https://www.skills.google/course_templates/2", "evidence": "Visible label"},
            "status": "pending",
        }]}
        self.env = {
            "RESEND_API_KEY": "test-key",
            "OWNER_EMAIL": "owner@example.com",
            "AGENT_REVIEW_SECRET": "a-test-secret-with-enough-randomness",
            "AGENT_REVIEW_BASE_URL": "https://example.netlify.app",
        }
        self.now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)

    def test_email_has_individual_approve_and_reject_links(self):
        subject, html, text = build_email(self.queue, self.env["AGENT_REVIEW_BASE_URL"], self.env["AGENT_REVIEW_SECRET"], int(self.now.timestamp()))
        self.assertIn("1 updates ready", subject)
        self.assertIn("Approve this update", html)
        self.assertIn("Reject this update", html)
        self.assertIn("action=approve", text)
        self.assertIn("action=reject", text)

    def test_signature_changes_when_action_changes(self):
        ts = int(self.now.timestamp())
        approve = review_signature("abc123", "approve", ts, self.env["AGENT_REVIEW_SECRET"])
        reject = review_signature("abc123", "reject", ts, self.env["AGENT_REVIEW_SECRET"])
        self.assertNotEqual(approve, reject)
        url = review_url("https://example.netlify.app", "abc123", "approve", ts, self.env["AGENT_REVIEW_SECRET"])
        self.assertIn("/api/agent-review?", url)

    def test_first_email_sends_and_sets_timestamp(self):
        with patch("agent.review_digest.urllib.request.urlopen", return_value=FakeResponse()):
            result, state = send_digest(self.queue, {"last_sent_at": None}, self.env, self.now)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(state["last_sent_item_count"], 1)

    def test_digest_is_not_resent_before_48_hours(self):
        state = {"last_sent_at": (self.now - timedelta(hours=12)).isoformat()}
        with patch("agent.review_digest.urllib.request.urlopen") as mocked:
            result, next_state = send_digest(self.queue, state, self.env, self.now)
        mocked.assert_not_called()
        self.assertEqual(result["status"], "not_due")
        self.assertEqual(next_state, state)

    def test_missing_review_endpoint_disables_email(self):
        env = dict(self.env)
        env.pop("AGENT_REVIEW_BASE_URL")
        result, state = send_digest(self.queue, {"last_sent_at": None}, env, self.now)
        self.assertEqual(result["status"], "disabled_missing_configuration")
        self.assertIn("AGENT_REVIEW_BASE_URL", result["missing"])


if __name__ == "__main__":
    unittest.main()
