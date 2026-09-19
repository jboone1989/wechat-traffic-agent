"""Bounded model-generated repair planning without execution authority."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GeneratedRepairEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=3, max_length=240)
    old_text: str = Field(min_length=1, max_length=5000)
    new_text: str = Field(min_length=1, max_length=5000)


class GeneratedRepairMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=3, max_length=240)
    marker: str = Field(min_length=3, max_length=500)


class GeneratedRepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis: str = Field(min_length=12, max_length=800)
    rationale: str = Field(min_length=12, max_length=1000)
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    confidence: float = Field(ge=0.0, le=1.0)
    target_files: list[str] = Field(min_length=1, max_length=2)
    edits: list[GeneratedRepairEdit] = Field(min_length=1, max_length=3)
    focused_tests: list[str] = Field(min_length=1, max_length=4)
    implementation_markers: list[GeneratedRepairMarker] = Field(min_length=1, max_length=4)


PLANNER_VERSION = "generated-repair.v2"


FORBIDDEN_TARGET_PARTS = (
    "self_repair", "self_maintenance", "generated_repair",
    "credential", "secret", "operator", "network", "auth", "deploy",
)
FORBIDDEN_GENERATED_TEXT = (
    "subprocess", "os.system", "popen(", "socket", "requests.", "httpx.",
    "shutil.rmtree", ".unlink(", "chmod(", "eval(", "exec(", "__import__",
    "api_key", "credential", "secret", "git push", "deploy",
)


class GeneratedRepairPlanner:
    def __init__(self, *, workspace_root: Path, runtime: Any) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.runtime = runtime

    @staticmethod
    def safe_source_path(value: str) -> bool:
        try:
            path = PurePosixPath(value)
        except Exception:
            return False
        if path.is_absolute() or ".." in path.parts:
            return False
        text = path.as_posix()
        if not text.startswith("src/wechat_traffic_agent/") or not text.endswith(".py"):
            return False
        lower = text.lower()
        return not any(part in lower for part in FORBIDDEN_TARGET_PARTS)

    def candidate_source_paths(self, problem: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        failure_site = str(problem.get("failure_site") or "")
        if failure_site and failure_site != "unknown":
            for frame in failure_site.split(">"):
                filename = frame.split(":", 1)[0].strip()
                if not filename.endswith(".py"):
                    continue
                relative = f"src/wechat_traffic_agent/{filename}"
                if self.safe_source_path(relative) and (self.workspace_root / relative).is_file():
                    if relative not in candidates:
                        candidates.append(relative)
            if candidates:
                return candidates[:2]

        symptom = " ".join(str(problem.get("symptom") or "").split())
        needles = [symptom]
        if ":" in symptom:
            prefix = symptom.rsplit(":", 1)[0].strip() + ":"
            if len(prefix) >= 12:
                needles.insert(0, prefix)
        hits: list[str] = []
        source_root = self.workspace_root / "src" / "wechat_traffic_agent"
        for path in sorted(source_root.glob("*.py")):
            relative = path.relative_to(self.workspace_root).as_posix()
            if not self.safe_source_path(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(needle and needle in text for needle in needles):
                hits.append(relative)
        return hits[:2] if 0 < len(hits) <= 2 else []

    def candidate_test_paths(self, problem: dict[str, Any], sources: list[str]) -> list[str]:
        tests_root = self.workspace_root / "tests"
        if not tests_root.is_dir():
            return []
        symptom_words = {
            word.lower().strip(" :,_-")
            for word in str(problem.get("symptom") or "").split()
            if len(word.strip(" :,_-")) >= 5
        }
        stems = {Path(path).stem for path in sources}
        scored: list[tuple[int, str]] = []
        for path in sorted(tests_root.glob("test_*.py")):
            relative = path.relative_to(self.workspace_root).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            lower = (relative + "\n" + text).lower()
            score = 0
            for stem in stems:
                if stem.lower() in lower:
                    score += 8
                if stem.startswith("content_") and "content" in path.name:
                    score += 2
            score += min(5, sum(1 for word in symptom_words if word in lower))
            if score:
                scored.append((score, relative))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = [path for _, path in scored[:8]]
        if not selected:
            fallback = tests_root / "test_content_loop.py"
            if fallback.is_file():
                selected.append(fallback.relative_to(self.workspace_root).as_posix())
        return selected

    def source_context(self, problem: dict[str, Any], relative: str) -> dict[str, Any]:
        path = self.workspace_root / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        symptom = " ".join(str(problem.get("symptom") or "").split())
        needles = [symptom]
        if ":" in symptom:
            prefix = symptom.rsplit(":", 1)[0].strip() + ":"
            if len(prefix) >= 12:
                needles.insert(0, prefix)
        index = None
        for i, line in enumerate(lines):
            if any(needle and needle in line for needle in needles):
                index = i
                break
        if index is None:
            failure_site = str(problem.get("failure_site") or "")
            for frame in failure_site.split(">"):
                parts = frame.split(":", 1)
                if len(parts) != 2 or parts[0] != Path(relative).name:
                    continue
                func = parts[1].strip()
                for i, line in enumerate(lines):
                    prefixes = ("def " + func + "(", "async def " + func + "(")
                    if line.lstrip().startswith(prefixes):
                        index = i
                        break
                if index is not None:
                    break
        index = 0 if index is None else index
        start = max(0, index - 45)
        end = min(len(lines), index + 55)
        snippet = "\n".join(lines[start:end])
        return {
            "path": relative,
            "start_line": start + 1,
            "end_line": end,
            "snippet": snippet[:12000],
        }

    @staticmethod
    def _extract_json(raw: str) -> str:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("repair planner did not return a JSON object")
        return text[start : end + 1]

    async def _ask(self, prompt: str) -> GeneratedRepairPlan:
        probe = getattr(self.runtime, "cognitive_probe", None)
        if not callable(probe):
            raise ValueError("repair planner runtime capability unavailable")
        raw = await probe(prompt, max_tokens=3000)
        try:
            return GeneratedRepairPlan.model_validate_json(self._extract_json(raw))
        except Exception as exc:
            raw = await probe(
                prompt
                + "\nReturn ONE corrected JSON object only. Validation error: "
                + str(exc)[:1200],
                max_tokens=3000,
            )
            return GeneratedRepairPlan.model_validate_json(self._extract_json(raw))

    def validate(
        self,
        plan: GeneratedRepairPlan,
        *,
        allowed_sources: list[str],
        allowed_tests: list[str],
    ) -> GeneratedRepairPlan:
        if plan.risk != "LOW":
            raise ValueError("generated repair risk must be LOW")
        if plan.confidence < 0.72:
            raise ValueError("generated repair confidence below 0.72")
        if len(set(plan.target_files)) != len(plan.target_files):
            raise ValueError("duplicate target files")
        if not set(plan.target_files).issubset(set(allowed_sources)):
            raise ValueError("target escaped localized source boundary")
        if not set(plan.focused_tests).issubset(set(allowed_tests)):
            raise ValueError("selected an unapproved test")
        prospective: dict[str, str] = {}
        for target in plan.target_files:
            if not self.safe_source_path(target):
                raise ValueError("unsafe target file")
            prospective[target] = (self.workspace_root / target).read_text(encoding="utf-8")
        for test in plan.focused_tests:
            if not (self.workspace_root / test).is_file():
                raise ValueError("focused test does not exist")

        total = 0
        for edit in plan.edits:
            if edit.path not in plan.target_files:
                raise ValueError("edit path not declared")
            if edit.old_text == edit.new_text:
                raise ValueError("edit makes no change")
            current = prospective[edit.path]
            if current.count(edit.old_text) != 1:
                raise ValueError("old_text must match exactly once")
            lower_new = edit.new_text.lower()
            if any(token in lower_new for token in FORBIDDEN_GENERATED_TEXT):
                raise ValueError("edit introduces forbidden capability")
            total += len(edit.old_text) + len(edit.new_text)
            if total > 9000:
                raise ValueError("repair exceeds change budget")
            prospective[edit.path] = current.replace(edit.old_text, edit.new_text, 1)

        for marker in plan.implementation_markers:
            if marker.path not in plan.target_files:
                raise ValueError("marker path not declared")
            if marker.marker not in prospective[marker.path]:
                raise ValueError("implementation marker absent after edits")
        return plan

    async def plan(
        self, problem: dict[str, Any]
    ) -> tuple[GeneratedRepairPlan, list[str], list[str]]:
        sources = self.candidate_source_paths(problem)
        tests = self.candidate_test_paths(problem, sources)
        if not sources or not tests:
            raise ValueError("insufficient localization")
        contexts = [self.source_context(problem, path) for path in sources]
        prompt = (
            "GENERATE_BOUNDED_SELF_REPAIR_PLAN. Return JSON only. "
            "Make the smallest reversible logic change. Edit ONLY allowed_source_paths; "
            "select focused tests ONLY from allowed_test_paths. source_context.snippet contains RAW source text "
            "without synthetic line-number prefixes; copy old_text VERBATIM from it and identify exactly one block. "
            "Do not add process execution, "
            "filesystem deletion, network access, credentials, auth, deployment, git push, "
            "self-maintenance changes, or authority changes. If evidence is insufficient, "
            "return low confidence rather than guessing. risk must be LOW for execution.\n"
            + json.dumps(
                {
                    "problem": {k: problem.get(k) for k in (
                        "problem_id", "symptom", "error_type", "failure_site",
                        "occurrences", "cycles", "evidence_refs",
                    )},
                    "allowed_source_paths": sources,
                    "allowed_test_paths": tests,
                    "source_context": contexts,
                    "schema": GeneratedRepairPlan.model_json_schema(),
                },
                ensure_ascii=False,
            )
        )
        plan = await self._ask(prompt)
        return self.validate(plan, allowed_sources=sources, allowed_tests=tests), sources, tests


__all__ = [
    "GeneratedRepairEdit", "GeneratedRepairMarker",
    "GeneratedRepairPlan", "GeneratedRepairPlanner", "PLANNER_VERSION",
]
