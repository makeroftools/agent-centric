"""The full production arc: model-as-agent + durable rules + crash-safe replay.

This is the easy-UX, runnable story of the whole subsystem:

1. An LLM is an ordinary agent in the tree — delegated to, re-verified by the
   parent, and audited with source references.
2. Intake is treated as a *hint*: an approved deterministic rule (authorized
   by a human once) auto-accepts matching drafts — no fresh human gate needed.
3. The whole session is recorded to a durable ledger and re-verified in a
   fresh process (crash-safe replay).

Everything runs offline and deterministically (the model is a stub by default).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent_centric.fbp import FbpDriver, replay_ledger
from agent_centric.fbp import store as store_mod
from agent_centric.fbp.bills_agent import (
    TASK_ACCEPT_DETERMINISTIC,
    TASK_CALENDAR,
    TASK_INTAKE,
    TASK_RULE_ADD,
)


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="fbp-arc-"))
    registry = workdir / "registry.db"
    ledger = workdir / "ledger.db"

    with FbpDriver(ledger_path=str(ledger)) as driver:
        # 1. A model agent in the tree (deterministic stub, source refs attached).
        driver.spawn("model", kind="model")
        model = driver.run("model", {"prompt": "summarize intake"}, child="model")
        print(f"model   : verified={model.verified} source={model.sources[0]['id']}")

        # 2. Bills loop with durable approved rules.
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {"state": str(registry), "store_keys": ["b-gas"]},
            child="bills",
        )
        # Authorize a deterministic rule once (durable via bills_rule_add).
        driver.run(
            TASK_RULE_ADD,
            {"rule": {"id": "r-gas", "domain": "vendor", "method": "from_vendor",
                      "matcher": {"vendor": "GasCo"}}},
            child="bills",
        )

        # 3. Intake an ambiguous-looking draft; the approved rule auto-accepts.
        draft = driver.run(
            TASK_INTAKE,
            {"draft": {"id": "b-gas", "vendor": "GasCo",
                       "amount_cents": 4500, "due_date": "2026-10-01"}},
            child="bills",
        )
        auto = driver.run(TASK_ACCEPT_DETERMINISTIC, {"draft": draft.value}, child="bills")
        print(f"auto    : verified={auto.verified} source={auto.sources[0]['id']}")

        # 4. Verified calendar projection.
        cal = driver.run(
            TASK_CALENDAR,
            {"from_date": "2026-10-01", "to_date": "2026-10-31"},
            child="bills",
        )
        print(f"calendar: total_cents={cal.value['total_cents']}")

    # 5. Crash-safe replay: re-verify the whole recorded session in a fresh
    #    process (auto-seeds the stub model callable from the ledger manifest).
    result = replay_ledger(str(ledger))
    print(f"replay  : ok={result['ok']} {result['passed']}/{result['runs']} runs")

    st = store_mod.open_state(registry)
    print(f"durable : registry b-gas status={st.get('b-gas')['status']}")
    st.close()


if __name__ == "__main__":
    main()