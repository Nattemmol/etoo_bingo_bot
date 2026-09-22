# Progress

## 2026-09-22 — Registration persistence

- Registration is permanently keyed by the Telegram user ID in the `users` table.
- Changed registration writes to an idempotent upsert: repeated contact messages update the same profile and preserve the existing wallet balance and transaction history.
- Added a safe migration for existing databases so prior player records gain profile update metadata without being deleted.
- Verified both repeat registration and legacy database migration with automated tests.
- Production must keep `DATABASE_PATH=/data/goodbingo.db` on the configured Fly or Render persistent volume. A deployment using an ephemeral filesystem will lose all registrations after a restart.

## 2026-09-22 — Server-side payment verification hardening

- Deposits now begin with a server-created PeerPayment checkout for Telebirr, CBE Birr, or CBE Mobile Banking. The server stores the checkout ownership, selected rail, amount, and provider order ID; it no longer accepts a browser-supplied checkout URL as proof or authorization.
- Receipt links and transaction IDs are evidence only. A reference is atomically reserved once across all orders, sent to PeerPayment using the server-stored checkout, and remains pending until PeerPayment verifies the intended receiver, exact amount/currency, freshness, and one-time use.
- Wallet crediting remains exclusively in the signed `deposit.succeeded` / `deposit.manually_succeeded` webhook path, keyed idempotently by the PeerPayment deposit ID. Unsigned webhooks (including test deliveries) and deployments without `PEERPAY_WEBHOOK_SECRET` are rejected.
- Withdrawals are now Telebirr-only. The server validates/normalizes the destination phone, creates a PeerPayment hosted confirmation, and atomically creates a reversible wallet hold only after the provider returns a real withdrawal ID. The hold is captured only on verified success and is released only on the documented terminal failure events.
- Updated bot, Mini App, and regression tests. Verified with `python -m unittest tests.test_peerpay_full tests.test_registration_persistence` (14 passing tests).
