# Wallet encryption v2: operator runbook

This code change does **not** migrate a deployed volume by itself. Migration runs
at the beginning of `bot.main()`, before Bot construction, Telegram command
registration, database/API startup, background sync, or polling. A failure aborts
startup; do not bypass it or replace the wallet file with `{}`.

## Required environment

- `WALLET_MASTER_KEY`: required even with no wallets. Generate **once** with
  `cryptography.fernet.Fernet.generate_key()` using a trusted, private operator
  workflow; it is URL-safe base64 encoding of 32 independently random bytes.
  Store the result directly in the platform's secret environment settings and
  maintain an encrypted offline recovery copy. Never put it in source control,
  chat, logs, command-line arguments, or this document. Do not reuse a Telegram
  token or derive this key from one. Never regenerate it on restart.
- `WALLET_LEGACY_BOT_TOKEN`: temporarily required only if any row lacks
  `encryption_version` or has integer `encryption_version: 1`. Use the **exact old
  Telegram token that encrypted those wallets**, not its rotated replacement.
- `BOT_TOKEN`: Telegram authentication only after migration; changing it has no
  effect on v2 encryption.
- `USERDATA_DIR`: the persistent directory containing the existing `wallets.json`.
  Confirm the correct volume is mounted; an absent file is treated as a fresh
  installation. Default is the repository's `data` directory.

Placeholders for operator configuration (not usable secrets):

| Variable | Placeholder |
| --- | --- |
| WALLET_MASTER_KEY | `<independently-generated-fernet-key>` |
| WALLET_LEGACY_BOT_TOKEN | `<exact-previous-telegram-token-for-one-time-migration>` |

## Controlled rollout

1. Stop every writer/old replica and prevent overlapping deployments. This is a
   **single-writer JSON store**, not a concurrent database; migration does not
   implement cross-process locking. Confirm the durable volume and target file
   exist where expected. Preserve a separate encrypted volume backup and the
   legacy token privately before replacing any Telegram credential.
2. Configure a stable master key plus the legacy token in secret settings.
   Deploy this code with that same persistent volume, then allow exactly one
   instance to start. Do not launch an old instance against migrated storage.
3. Startup validates master format, JSON (rejecting duplicate keys and non-finite
   constants), ownership/schema, and supported versions. If legacy rows exist,
   it creates a unique `wallets.json.pre-v2-*.bak` containing the **original exact
   encrypted bytes**, permission `0600`, and fsyncs file and directory before
   decryption/conversion. Invalid master/schema or a missing required legacy
   secret aborts before backup/rewrite; a bad cipher/key/address aborts after the
   encrypted backup, without changing the original wallet file.
4. Every legacy and existing v2 row must decrypt and produce its stored address.
   Only legacy ciphertext is replaced, using per-user, domain-separated
   HMAC-SHA256 keys derived from the master. Each changed row is marked with
   integer `encryption_version: 2`. All fields/labels, user IDs, and per-user
   counts are preserved. New ciphertext must roundtrip to the original private
   key and matching address before commit.
5. Commit uses a same-directory temporary file, mode `0600`, file fsync, atomic
   `os.replace`, then directory fsync. If replacement fails, the original remains
   untouched and temporary files are cleaned. The encrypted backup is retained.
   If directory fsync fails **after** replacement, startup aborts but the complete
   verified v2 file may already be installed; retry with the same master key,
   never assume the old file remains. Atomicity does not guarantee filesystem or
   volume durability after hardware failure.
6. Verify only non-sensitive metadata: owner/row counts match the preserved
   backup; every row has integer version 2; backup and active file are mode 0600.
   A successful startup has already verified all private-key/address pairs.
   Do not print ciphertext, decrypted keys, environment values, or traceback
   locals. Wallet operations intentionally expose only a generic error.
7. Remove `WALLET_LEGACY_BOT_TOKEN` from the service environment after verifying
   success, then restart with the **same master**. V2-only startup verifies all
   rows without rewriting the file or creating another backup. Telegram token
   rotation is now independent. Keep the encrypted legacy backup and old token
   together in protected recovery storage under an explicit retention policy;
   legacy backups remain dependent on that old token even if Telegram revoked it.

## Failure and rollback

- Never delete an invalid row, silently reset corrupted JSON, or retry using an
  invented master/legacy token. Invalid JSON, schema, version, ciphertext, master,
  private key, and address mismatches all fail closed for the whole store.
- Runtime loading is v2-only and verifies the entire store before returning any
  data; it will not silently map corrupt storage to an empty wallet list.
- Restarting after a successful or interrupted atomic replacement is idempotent.
  Failed attempts before replacement may leave multiple encrypted backup files;
  preserve them until recovery is complete.
- Roll back only with all writers stopped: restore a known-good original
  encrypted backup plus the matching old token **and compatible old code**, or
  restore the v2 file plus the exact matching master and this code. Never point
  old code at v2 rows. Restoring a backup can lose later wallet additions/edits.
- Master-key rotation is **not implemented**. Keep the existing master stable;
  a lost master makes v2 wallets unrecoverable without independent wallet backups.
- This migration touches only the local signing-wallet file. It does not modify
  tracked/public-wallet database tables or invoke an RPC/API. Filesystem access
  is trusted; platform permissions and safe backup storage remain operator duties.

## Offline verification

In an isolated checkout/virtual environment with dependencies installed:

```sh
python -m pytest tests/test_wallet_encryption.py -q
python -m pytest tests -q
```

Tests use generated disposable accounts and temporary files. The startup test
parses `bot.py` without importing or invoking it. Database/API integration tests
are skipped when `TEST_DATABASE_URL` is unset; do not point them at production.
