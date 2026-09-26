#!/usr/bin/env python3
"""Run the bounded regression set for durable wakeful cognition + production parity."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

TESTS = [
    "tests/test_livingruntime_chatgpt.py",
    "tests/test_cognition_context.py",
    "tests/test_owner_agent_episode.py",
    "tests/test_wakeful_chatgpt_resume.py",
    "tests/test_wakeful_runtime.py",
    "tests/test_wakeful_observe_world.py",
    "tests/test_organism_closure.py",
    "tests/test_storage_reclaim_backoff.py",
    "tests/test_storage_pressure_modes.py",
    "tests/test_neural_motor.py",
    "tests/test_prospective_predictions.py",
    "tests/test_x_growth_mission.py",
    "tests/test_x_social.py",
    "tests/test_content_explorer_loop.py",
    "tests/test_content_runtime_app.py",
    "tests/test_runtime_import_binding.py",
    "tests/test_cognition_context.py",
    "tests/test_owner_agent_episode.py",
    "tests/test_owner_mission_output_fallback.py",
    "tests/test_owner_cognition.py",
    "tests/test_owner_prompt_budget.py",
    "tests/test_operator_private_chat.py",
    "tests/test_awe_production_contestant.py",
    "tests/test_survival_resources.py",
    "tests/test_survival_storage_reclaim.py",
    "tests/test_storage_lifecycle.py",
    "tests/test_trading_network_source.py",
    "tests/test_trading_draft_worker.py",
    "tests/test_live_agent.py",
]


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", *TESTS]))
