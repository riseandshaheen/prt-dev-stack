"""Scenario catalog. Each module has ID, TITLE, OUTCOME and run(h)."""

from importlib import import_module

ORDER = ["g0_golden", "r1_join_race", "r2_stage_race", "r3_accept_race", "r4_sentry_path",
         "c4_forged_sentry", "r5_twin_defenders", "r5b_defender_absent", "t3_chain_reset", "t3b_resume_after_reset"]


def load(names=None):
    return [import_module(f"{__name__}.{n}") for n in (names or ORDER)]
