# SocialPlan — Free-tier persistence migration plan

Status: design only. No database account, paid service, production data migration, or Render environment change has been made.

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
