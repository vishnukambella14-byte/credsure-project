# backend/keys: issuer signing keys (local development only)

This directory holds the **Ed25519 private keys** that CredSure issuers use to sign credentials.
Only this README is tracked by Git. Everything else in this directory is ignored (`.gitignore`:
`backend/keys/*` with `!backend/keys/README.md`; `*.pem` and `*.key` are also ignored everywhere).

No keys are created by setting up the repository. Keys are written by the application (issuer
creation) and by the synthetic data generator.

## Layout

| Path | Contents | Written by |
|------|----------|------------|
| `backend/keys/platform/<signing_key_id>.pem` | Private keys of **platform** issuers (`ISS-001`–`ISS-899`) | `POST /api/v1/issuers`. Exclusive create: an existing file is never overwritten. |
| `backend/keys/synthetic/<profile>/<signing_key_id>.pem` | Private keys of **synthetic** issuers (`ISS-901`–`ISS-999`) for the `starter` / `full` datasets | `python -m app.datagen generate` only |

The signing service chooses the directory from the issuer's `record_source` (`platform` or
`synthetic_import`). See [ADR-005](../../docs/architecture_decisions.md#adr-005-private-keys-in-git-ignored-files-public-keys-in-the-database)
and [ADR-024](../../docs/architecture_decisions.md#adr-024-synthetic-issuer-and-key-isolation).

## Rules

1. **Never commit a private key.** Do not add exceptions to `.gitignore` for anything in this directory except this README. Before committing, check with `git status` that no `.pem`, `.key` or key directory appears.
2. **Keep synthetic keys separate from platform keys.** The synthetic data generator writes only under `synthetic/<profile>/`. It must never open, read, write, reuse or delete anything under `platform/`. Synthetic keys are derived from the public seed (`20261022`), so anyone can recreate them: they are **forgeable by design** and must never represent a real institution.
3. **Private keys never leave this directory through the application.** They are not stored in the database, not logged, and never returned by the API. The database stores only the public key (`issuers.public_key`).
4. **No production secrets in source control.** These files are unencrypted PKCS8 PEM keys for local development and demonstration. A production deployment would keep signing keys in a secrets manager or HSM, never in the repository or in `.env` files.
5. **Losing a key** stops new issuance for that issuer (`503 signing_key_unavailable`) but not verification: verification uses the public key stored in the database.
