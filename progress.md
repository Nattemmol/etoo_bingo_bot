# Progress

## 2026-09-22 — Registration persistence

- Registration is permanently keyed by the Telegram user ID in the `users` table.
- Changed registration writes to an idempotent upsert: repeated contact messages update the same profile and preserve the existing wallet balance and transaction history.
- Added a safe migration for existing databases so prior player records gain profile update metadata without being deleted.
- Verified both repeat registration and legacy database migration with automated tests.
- Production must keep `DATABASE_PATH=/data/goodbingo.db` on the configured Fly or Render persistent volume. A deployment using an ephemeral filesystem will lose all registrations after a restart.
