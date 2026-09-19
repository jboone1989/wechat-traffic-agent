"""Bounded model-generated repair planning without execution authority."""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GeneratedRepairEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default="", max_length=240)
    block_id: str = Field(min_length=12, max_length=80)
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
    target_files: list[str] = Field(default_factory=list, max_length=2)
    edits: list[GeneratedRepairEdit] = Field(min_length=1, max_length=3)
    focused_tests: list[str] = Field(default_factory=list, max_length=4)
    implementation_markers: list[GeneratedRepairMarker] = Field(default_factory=list, max_length=4)


class EditableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    block_id: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    old_text: str


PLANNER_VERSION = "generated-repair.v7"

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

    def _counterevidence_test_paths(self, problem: dict[str, Any]) -> list[str]:
        trial = problem.get("repair_trial")
        if not isinstance(trial, dict):
            return []
        selected: list[str] = []
        for value in trial.get("failed_tests") or ():
            test_id = str(value or "").strip()
            relative = test_id.split("::", 1)[0].strip()
            try:
                path = PurePosixPath(relative)
            except Exception:
                continue
            if (
                path.is_absolute()
                or ".." in path.parts
                or len(path.parts) != 2
                or path.parts[0] != "tests"
                or not path.name.startswith("test_")
                or path.suffix != ".py"
            ):
                continue
            normalized = path.as_posix()
            if not (self.workspace_root / normalized).is_file():
                continue
            if normalized not in selected:
                selected.append(normalized)
            if len(selected) >= 4:
                break
        return selected

    def candidate_test_paths(self, problem: dict[str, Any], sources: list[str]) -> list[str]:
        tests_root = self.workspace_root / "tests"
        if not tests_root.is_dir():
            return []
        counterevidence = self._counterevidence_test_paths(problem)
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
        selected = list(counterevidence)
        for path in (path for _, path in scored):
            if path not in selected:
                selected.append(path)
            if len(selected) >= 10:
                break
        if not selected:
            fallback = tests_root / "test_content_loop.py"
            if fallback.is_file():
                selected.append(fallback.relative_to(self.workspace_root).as_posix())
        return selected

    @staticmethod
    def _failure_functions(problem: dict[str, Any], relative: str) -> set[str]:
        names: set[str] = set()
        filename = Path(relative).name
        for frame in str(problem.get("failure_site") or "").split(">"):
            parts = frame.split(":", 1)
            if len(parts) == 2 and parts[0].strip() == filename:
                names.add(parts[1].strip())
        return names

    def editable_blocks(
        self, problem: dict[str, Any], sources: list[str]
    ) -> list[EditableBlock]:
        symptom = " ".join(str(problem.get("symptom") or "").split())
        rows: list[tuple[int, EditableBlock]] = []
        for relative in sources:
            path = self.workspace_root / relative
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            lines = source.splitlines(keepends=True)
            failure_functions = self._failure_functions(problem, relative)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.end_lineno is None:
                    continue
                old_text = "".join(lines[node.lineno - 1 : node.end_lineno])
                if not old_text or len(old_text) > 5000 or source.count(old_text) != 1:
                    continue
                score = 0
                if node.name in failure_functions:
                    score += 20
                if symptom and symptom in old_text:
                    score += 12
                if not score:
                    continue
                digest = sha256((relative + "\0" + old_text).encode("utf-8")).hexdigest()[:24]
                rows.append((
                    score,
                    EditableBlock(
                        block_id=f"block:{digest}",
                        path=relative,
                        symbol=node.name,
                        start_line=node.lineno,
                        end_line=node.end_lineno,
                        old_text=old_text,
                    ),
                ))
        rows.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line))
        seen: set[str] = set()
        result: list[EditableBlock] = []
        for _, block in rows:
            if block.block_id in seen:
                continue
            seen.add(block.block_id)
            result.append(block)
            if len(result) >= 6:
                break
        return result

    def source_context(self, problem: dict[str, Any], relative: str) -> dict[str, Any]:
        path = self.workspace_root / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        symptom = " ".join(str(problem.get("symptom") or "").split())
        for i, line in enumerate(lines):
            if symptom and symptom in line:
                index = i
                break
        else:
            functions = self._failure_functions(problem, relative)
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if any(stripped.startswith(("def " + f + "(", "async def " + f + "(")) for f in functions):
                    index = i
                    break
        start = max(0, index - 30)
        end = min(len(lines), index + 45)
        return {
            "path": relative,
            "start_line": start + 1,
            "end_line": end,
            "snippet": "\n".join(lines[start:end])[:9000],
        }

    @staticmethod
    def _extract_json(raw: str) -> str:
        text = str(raw or "").strip()
        fence = chr(96) * 3
        if text.startswith(fence):
            text = text.strip(chr(96)).strip()
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

    def _bind_authoritative_boundaries(
        self,
        plan: GeneratedRepairPlan,
        *,
        allowed_sources: list[str],
        allowed_tests: list[str],
        editable_blocks: list[EditableBlock],
    ) -> GeneratedRepairPlan:
        """Derive source and test authority from runtime-owned evidence, never model text."""
        block_map = {row.block_id: row for row in editable_blocks}
        bound_edits: list[GeneratedRepairEdit] = []
        target_files: list[str] = []
        for edit in plan.edits:
            block = block_map.get(edit.block_id)
            if block is None:
                raise ValueError("generated edit referenced unknown block_id")
            if block.path not in allowed_sources:
                raise ValueError("generated edit escaped localized source boundary")
            bound_edits.append(edit.model_copy(update={"path": block.path}))
            if block.path not in target_files:
                target_files.append(block.path)
        if not target_files:
            raise ValueError("generated repair selected no authoritative source block")
        focused_tests = list(allowed_tests[:4])
        if not focused_tests:
            raise ValueError("generated repair has no authoritative focused tests")
        return plan.model_copy(
            update={
                "target_files": target_files,
                "edits": bound_edits,
                "focused_tests": focused_tests,
                "implementation_markers": [],
            }
        )

    def validate(
        self,
        plan: GeneratedRepairPlan,
        *,
        allowed_sources: list[str],
        allowed_tests: list[str],
        editable_blocks: list[EditableBlock],
    ) -> GeneratedRepairPlan:
        if plan.risk != "LOW":
            raise ValueError("generated repair risk must be LOW")
        if plan.confidence < 0.72:
            raise ValueError("generated repair confidence below 0.72")
        plan = self._bind_authoritative_boundaries(
            plan,
            allowed_sources=allowed_sources,
            allowed_tests=allowed_tests,
            editable_blocks=editable_blocks,
        )
        block_map = {row.block_id: row for row in editable_blocks}
        prospective = {
            target: (self.workspace_root / target).read_text(encoding="utf-8")
            for target in plan.target_files
        }
        for target in plan.target_files:
            if not self.safe_source_path(target):
                raise ValueError("unsafe target file")
        for test in plan.focused_tests:
            if not (self.workspace_root / test).is_file():
                raise ValueError("focused test does not exist")

        total = 0
        used_blocks: set[str] = set()
        for edit in plan.edits:
            block = block_map.get(edit.block_id)
            if block is None:
                raise ValueError("generated edit referenced unknown block_id")
            if edit.block_id in used_blocks:
                raise ValueError("generated edit reused block_id")
            used_blocks.add(edit.block_id)
            if edit.path not in plan.target_files or block.path != edit.path:
                raise ValueError("generated edit escaped block path")
            current = prospective[edit.path]
            if current.count(block.old_text) != 1:
                raise ValueError("editable block is stale or ambiguous")
            if block.old_text == edit.new_text:
                raise ValueError("edit makes no change")
            lower_new = edit.new_text.lower()
            if any(token in lower_new for token in FORBIDDEN_GENERATED_TEXT):
                raise ValueError("edit introduces forbidden capability")
            total += len(block.old_text) + len(edit.new_text)
            if total > 9000:
                raise ValueError("repair exceeds change budget")
            prospective[edit.path] = current.replace(block.old_text, edit.new_text, 1)

        return plan

    @staticmethod
    def materialize_edits(
        plan: GeneratedRepairPlan, editable_blocks: list[EditableBlock]
    ) -> list[tuple[str, str, str]]:
        block_map = {row.block_id: row for row in editable_blocks}
        return [
            (edit.path, block_map[edit.block_id].old_text, edit.new_text)
            for edit in plan.edits
        ]

    async def plan(
        self, problem: dict[str, Any]
    ) -> tuple[GeneratedRepairPlan, list[str], list[str], list[EditableBlock]]:
        sources = self.candidate_source_paths(problem)
        tests = self.candidate_test_paths(problem, sources)
        blocks = self.editable_blocks(problem, sources)
        if not sources or not tests or not blocks:
            raise ValueError("insufficient localization")
        contexts = [self.source_context(problem, path) for path in sources]
        prompt = (
            "GENERATE_BOUNDED_SELF_REPAIR_PLAN. Return JSON only. "
            "Make the smallest reversible logic change. Source paths and focused tests are runtime-owned authority. "
            "Leave target_files and focused_tests empty; leave each edit.path empty. Do NOT copy old source text. "
            "For each edit, choose exactly one server-generated editable_blocks.block_id and provide replacement new_text. "
            "The runtime derives edit.path and target_files from block_id, and focused_tests from allowed_test_paths. "
            "The block_id cryptographically binds the authoritative old_text and becomes invalid if source changes. "
            "Do not invent adoption markers: leave implementation_markers empty; the runtime derives them from validated new_text. "
            "Do not add process execution, filesystem deletion, network access, credentials, auth, deployment, git push, "
            "self-maintenance changes, or authority changes. If evidence is insufficient, return low confidence rather "
            "than guessing. risk must be LOW for execution.\n"
            + json.dumps(
                {
                    "problem": {k: problem.get(k) for k in (
                        "problem_id", "symptom", "error_type", "failure_site",
                        "occurrences", "cycles", "evidence_refs",
                    )},
                    "prior_repair_trial": problem.get("repair_trial"),
                    "instruction_from_counterevidence": (
                        "If prior_repair_trial is present, treat its failed tests and assertion evidence "
                        "as counterevidence against the previous hypothesis. Do not repeat the rejected edit; "
                        "propose a materially different bounded fix that preserves the failed contracts."
                    ),
                    "allowed_source_paths": sources,
                    "allowed_test_paths": tests,
                    "source_context": contexts,
                    "editable_blocks": [
                        row.model_dump(mode="json") for row in blocks
                    ],
                    "schema": GeneratedRepairPlan.model_json_schema(),
                },
                ensure_ascii=False,
            )
        )
        plan = await self._ask(prompt)
        return (
            self.validate(
                plan,
                allowed_sources=sources,
                allowed_tests=tests,
                editable_blocks=blocks,
            ),
            sources,
            tests,
            blocks,
        )


__all__ = [
    "EditableBlock", "GeneratedRepairEdit", "GeneratedRepairMarker",
    "GeneratedRepairPlan", "GeneratedRepairPlanner", "PLANNER_VERSION",
]
