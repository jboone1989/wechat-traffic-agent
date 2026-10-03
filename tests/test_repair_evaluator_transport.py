import json
from datetime import datetime,timezone
import pytest
from wechat_traffic_agent.self_repair_executor import SelfRepairExecutor,DEFAULT_RECIPES
from wechat_traffic_agent.self_maintenance import SelfMaintenanceService
from test_self_maintenance import EvolutionRuntime
from test_self_repair_executor import RequestCapability,_problem
@pytest.mark.parametrize("entrypoint",["maintenance","executor"])
@pytest.mark.parametrize("kind",["generated","explicit","builtin"])
def test_both_repair_submission_paths_preserve_authoritative_evaluators(tmp_path,entrypoint,kind):
    path=_problem(tmp_path);problem=json.loads(path.read_text())
    proposal=problem["repair_proposal"]
    if kind=="generated":
        proposal["repair_kind"]="GENERATED_BOUNDED_EDIT"
        proposal["generated_recipe"]={"focused_tests":["tests/test_generated_repair.py"]}
        expected=["tests/test_generated_repair.py"]
    elif kind=="explicit":
        proposal["repair_kind"]="USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION"
        proposal["focused_tests"]=["tests/test_content_agent.py"];expected=proposal["focused_tests"]
    else:
        expected=list(DEFAULT_RECIPES[proposal["repair_kind"]].focused_tests)
    observed_failed=["tests/test_prior_failure.py::test_actual_failure"]
    problem["repair_trial"]={"failed_tests":observed_failed};path.write_text(json.dumps(problem))
    runtime=RequestCapability()
    if entrypoint=="maintenance":
        service=SelfMaintenanceService(state_root=tmp_path,evolution_runtime=EvolutionRuntime(),capability_runtime=runtime)
        service.request_code_repair(problem["problem_id"])
    else:
        service=SelfRepairExecutor(state_root=tmp_path,workspace_root=None,deployed_root=None,capability_runtime=runtime)
        service.run_once(now=datetime(2026,10,3,tzinfo=timezone.utc))
    request=runtime.calls[0][1]
    assert request["evaluationTests"]==expected
    assert request["failedTests"]==observed_failed
