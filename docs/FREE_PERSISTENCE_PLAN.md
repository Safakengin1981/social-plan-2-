# SocialPlan — Free-tier persistence migration plan

Status: isolated PostgreSQL API and CI integration tests implemented; GitHub Actions run 43450430932 passed. No external database account, paid service, production data migration, or Render environment change has been made.

## Existing system
- Current app uses sqlite3 connections and SQLite-only SQL (PRAGMA, executescript, BEGIN IMMEDIATE, strftime, positional ? placeholders, INSERT OR CONFLICT, lastrowid).
- User accounts, session hashes, posts, audit events, queue jobs, OAuth states and encrypted Instagram tokens are in the same SQLite database.
- Uploaded media is stored on the instance filesystem.
- Free Render instance has no persistent disk. CSV export is not a full backup.

## Target architecture
- Render Free: stateless FastAPI process only.
- External free-tier PostgreSQL (provider selected after confirming current plan terms) for durable transactional records.
- Private object storage for uploaded media, with tenant-specific keys and authenticated access.
- Separate encrypted backups, restore drill and retention policy.

## Migration checklist
1. Select a provider and verify free quota, retention, inactivity/suspension rules, network/TLS requirements and backups.
2. Create an isolated empty test database; keep credentials only in Render secret environment variables.
3. Introduce database abstraction and parameterized SQL; migrate SQLite-specific schema and queries; retain tenant filters.
4. Make schema initialization explicit and versioned; no automatic destructive migration.
5. Move media upload/read to private object storage, verify content signatures and tenant authorization.
6. Test register/login, lockouts, sessions, member management, post create/approve/cancel, queue, audit, OAuth states, token encryption and media.
7. Verify restart/redeploy persistence and a backup restoration in a separate environment.
8. Only then switch the isolated Instagram test service, with a rollback plan. Do not modify the existing persistent staging service.

## Acceptance criteria
- No account, post, or media disappears across a redeploy.
- Concurrent writes are transactional; audit and post changes are atomic.
- No credentials or personal data are committed to Git.
- Test account and current content are exported before switching.
- No automatic publishing until Meta OAuth permissions are approved and tested.

## Verified CI baseline (2026-11-13)
- Passing workflow: https://github.com/Safakengin1981/social-plan-2-/actions/runs/43450430932
- PostgreSQL 16 schema, tenant constraints, cross-process persistence, API integration and dump/restore were tested against disposable CI databases.
- This is **not** evidence of persistence across Render redeploys or an external free provider's retention policy.

## Isolated local startup (not production)
Install `pip install -r requirements-pg-test.txt`. Configure a fresh test PostgreSQL instance and a **private** S3-compatible test bucket. Apply `migrations/postgres/001_initial.sql` to that empty database. Set `DATABASE_URL`, `SOCIALPLAN_S3_ENDPOINT`, `SOCIALPLAN_S3_BUCKET`, `SOCIALPLAN_S3_ACCESS_KEY`, `SOCIALPLAN_S3_SECRET_KEY`, and `SOCIALPLAN_FERNET_KEY` as secret environment variables. Run `uvicorn pg_api:app --host 127.0.0.1 --port 8001` and open `http://127.0.0.1:8001/pg-test`.

Do not use the existing Render service's SQLite data or environment credentials. Never commit environment variable values. The separate API does not implement Instagram publishing, and external storage accounts have not been provisioned.

## Go/no-go before any isolated Render deployment
- Confirm provider free-tier pricing and suspend/delete policy.
- Verify database TLS and least-privilege credentials.
- Test upload, readback, checksum, unauthorized tenant access and 25 MB limits against actual private bucket.
- Record baseline counts and take a downloadable backup.
- Redeploy the isolated test service and verify post, session and media continuity.
- Restore backup into a **different** disposable database and compare record counts.
- Only deploy after explicit approval of the external provider connection and target isolated service; leave main and staging untouched.

## Free provider candidates verified 2026-11-13
- **Neon Free PostgreSQL**: 1 GB per project, 100 CU-hours/project/month; compute suspends after 5 minutes of inactivity. See https://neon.com/pricing and https://neon.com/docs/introduction/scale-to-zero. Verify quota and inactivity policies again before creating an account.
- **Cloudflare R2 Standard**: 10 GB-month storage, 1 million Class A operations, 10 million Class B operations per month included. Usage above allowance may incur charges; activating R2 requires a billing subscription/checkout flow. See https://developers.cloudflare.com/r2/pricing/ and https://developers.cloudflare.com/r2/get-started/.
- **Decision**: candidate only, not provisioned or connected. Do not enroll in any billable plan, enter payment details, or create resources without user approval.
- **Security**: store Neon TLS connection URL and R2 private bucket keys only in secret environment variables; never expose them to the browser. Test cross-tenant access and checksum enforcement before deploying.
- **Cost guardrail**: configure usage alerts or account-level spending controls where available; alerts alone do not prevent overages. If zero possible charges is a strict requirement, do not activate R2 until billing risk is explicitly accepted or a no-billing alternative is selected.
