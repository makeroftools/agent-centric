# designs/ — declarative `design.v1` sources

`designs/core.v1.json` is the **Core umbrella design**: the shell → registry
spine that `components.lock` pins. It is the reviewed, human-authored input to
the producer; the lock is the deterministic output.

## Umbrella lock (offline directory source + local signing)

SPEC-0011: the Core umbrella lock is built from an **offline directory source**
with **local signing**, and the lock-level signature is verified at boot.

- `components.lock` — the pinned component set (`components.lock/v1`).
- `components.lock.sig` — the detached **lock** signature over `lock_hash`.
- `artifacts/components/{shell,registry}.tar` — the canonical, content-addressed
  bundles (`tree_sha256` = sha256 of the bundle bytes); a **directory source**
  serves exactly these bytes.

Regenerate deterministically (whole-file, Law 11):

```sh
tools/umbrella-lock.sh
```

## Keys

`tools/umbrella-lock.sh` defaults to a deterministic **offline test key**
(`--key offline-test`) so the slice runs and tests without key material. The
lock and bundles are test-signed, not production-trustworthy.

The operator regenerates with the real, out-of-process key and publishes only
the **public** trust root:

```sh
tools/umbrella-lock.sh --key gpg --key-id <operator-key-id>
```

The component-signing private key never enters the repo or the session. Retention
of keys is handled by `cbp/trust.py` windows (SPEC-0007 §6).
