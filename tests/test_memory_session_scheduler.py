import asyncio
from datetime import timedelta

import pytest
from agent_runtime.memory.session import MemoryBudgetExhausted, MemoryRequestBudgetExceeded
from test_content_loop import setup, NOW

pytestmark = pytest.mark.usefixtures('maintenance_cognition')

@pytest.mark.parametrize('error_type', [MemoryBudgetExhausted, MemoryRequestBudgetExceeded])
def test_exhausted_retrieval_is_terminal_and_does_not_reset_next_slice(tmp_path, error_type):
    runtime, worker, publisher = setup(tmp_path)
    calls = []
    async def exhausted(folder, cycle, now):
        calls.append(cycle)
        raise error_type('Explicit exhausted task checkpoint')
    worker._run = exhausted
    result = asyncio.run(worker.run_once(now=NOW, cycle_id='v2-exhausted-1790000000'))
    assert result['status'] == 'MEMORY_BUDGET_EXHAUSTED'
    assert result['retryable'] is False
    assert result['termination'] == 'BUDGET_EXHAUSTED'
    replay = asyncio.run(worker.run_once(now=NOW + timedelta(hours=1), cycle_id='v2-exhausted-1790000000'))
    assert replay['status'] == 'ALREADY_PROCESSED'
    assert replay['previous_status'] == 'MEMORY_BUDGET_EXHAUSTED'
    assert calls == ['v2-exhausted-1790000000']
