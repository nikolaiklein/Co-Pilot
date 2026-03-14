# Project Conventions

Accepted patterns and enforced conventions from council reviews. The council reads this file before every review to avoid re-flagging resolved decisions.

---

## Enforced Conventions

These must be followed — flag violations as findings.

### EC-1: Webhook secret token validation
**Convention:** All webhook endpoints must validate the `X-Telegram-Bot-Api-Secret-Token` header. Reject requests with missing/wrong tokens with 403.
**Origin:** Hunt — Council Review 2026-03-14-2238
**Principle:** `references/security.md` → Principle 7

### EC-2: Cron/debug endpoint authentication
**Convention:** All `/cron/*` and `/debug/*` endpoints must be protected by shared secret header check or IAM. No unauthenticated access to internal endpoints.
**Origin:** Hunt — Council Review 2026-03-14-2238
**Principle:** `references/security.md` → Principle 7

### EC-3: .dockerignore required
**Convention:** Project must have a `.dockerignore` excluding `.env*`, `.git/`, `__pycache__/`, `.council/`, `*.md`, `.venv/`.
**Origin:** Docker/Deploy Expert — Council Review 2026-03-14-2238
**Principle:** Docker best practices — container security

### EC-4: LLM call logging mandatory
**Convention:** All LLM calls across all providers must log model, token usage, latency, and finish_reason. No provider may silently swallow call metadata.
**Origin:** Willison — Council Review 2026-03-14-2238
**Principle:** `references/quality-llm.md` → Principle 4

### EC-5: Destructive commands require confirmation
**Convention:** Destructive commands (`/correct`, `/clear`, and any future data-modifying commands) must show a confirmation step with inline keyboard before executing. No single-command irreversible actions.
**Origin:** Friedman — Council Review 2026-03-14-2238
**Principle:** `references/quality-ux.md` → Principle 8

### EC-6: Webhook must return proper HTTP status codes
**Convention:** Webhook endpoint must return HTTP 500 for transient errors so Telegram retries. Never return 200 with error payload.
**Origin:** Backend/Python Expert — Council Review 2026-03-14-2238
**Principle:** `references/quality-backend.md` → Principle 3

### EC-7: DB write failures must propagate
**Convention:** Database write failures (`save_message`, `update_user`, `save_report`) must propagate exceptions to callers, not silently `pass`. Callers must inform the user of failure.
**Origin:** Backend/Python Expert — Council Review 2026-03-14-2238
**Principle:** `references/quality-backend.md` → Principle 1

### EC-8: No internal details in error responses
**Convention:** Error responses (HTTP and Telegram) must never contain `str(e)` or raw exception text. Use generic error messages in all user/client-facing responses. Log full exceptions server-side only.
**Origin:** Hunt — Council Review 2026-03-14-2238
**Principle:** `references/security.md` → Principle 9
