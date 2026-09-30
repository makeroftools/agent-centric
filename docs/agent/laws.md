# Laws — index (not authoritative)

[`PRINCIPLES.md`](../../PRINCIPLES.md) is the constitution. This page is a
one-line index so an agent can locate the right law fast; it never overrides the
source. If this index and the constitution disagree, **the constitution wins.**

| # | Law | One line |
| --- | --- | --- |
| 1 | Correctness First | Prefer the more verifiable, isolated, auditable path. |
| 2 | Deterministic Control Plane | Same inputs ⇒ same trajectory and outcome. |
| 2a | Abstract System — Not a Trading System | Do not frame this project as trading. |
| 3 | Agent-Centric Design | No central manager; a rooted, recursive tree. |
| 4 | Progressive Disclosure | Minimal interfaces; reveal detail only when needed. |
| 5 | Local-First | In-process, local, no network required for the core. |
| 6 | Full Auditability | Everything that matters is recorded and replayable. |
| 7 | Least Privilege | Grants are hard bounds, not advice. |
| 8 | Explicit, Audited Failure | Verified result or audited failure — no third state. |
| 9 | Critical Path Is the Deterministic Scheduler | Pure function of the network; may drive the schedule; never bypasses a verifier. |
| 10 | Registries Passive; Evidence Immutable | Catalogs never decide; evidence is write-once. |
| 11 | No In-Place File Edits, Ever | Whole-file atomic replace only — see [`editing.md`](editing.md). |
| 12 | Test Authority | The agent may run any tests; report faithfully; operator/CI is the record. |
| 13 | Commit and Push Continuously | Commit often, push often; hooks/CI guard; never bypass. |

## Adding a law

Laws are amended only by editing [`PRINCIPLES.md`](../../PRINCIPLES.md) through a
reviewed commit. Add the matching one-line row here in the same change so the
index never drifts.
