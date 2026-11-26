# Agent worker

See the root `README.md` and `PHONE_UPLOAD_GUIDE.md`.

The worker performs bounded public-page discovery and OCR. It automatically verifies only explicit visible labels:

- `SKILL BADGE` → Skill Badge catalogue entry.
- `ARCADE GAME` or `GAME BADGE` → Arcade game entry / game keyword.
- `COMPLETION BADGE` → separate completion-badge group.
- No clear label → unresolved candidate for review.

No point value, including a special +2 value, is inferred. No generative-AI API is called. The worker does not crawl user profiles.
