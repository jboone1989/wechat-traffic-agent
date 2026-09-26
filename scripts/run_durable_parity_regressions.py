#!/usr/bin/env python3
"""Run the bounded regression set for durable wakeful cognition + production parity."""
from __future__ import annotations

import pytest

TESTS = [
    "tests/test_livingruntime_chatgpt.py",
    "tests/test_wakeful_chatgpt_resume.py",
    "tests/test_wakeful_runtime.py",
    "tests/test_neural_motor.py",
    "tests/test_prospective_predictions.py",
    "tests/test_x_growth_mission.py",
    "tests/test_owner_cognition.py",
    "tests/test_owner_prompt_budget.py",
    "tests/test_operator_private_chat.py",
    "tests/test_awe_production_contestant.py",
    "tests/test_live_agent.py",
]


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", *TESTS]))
