import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError
from wechat_traffic_agent.x_discovery import XSearchPlan, discovery_interests, plan_x_search
from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport


class TestDomainSearch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_arbitrary_multilingual_queries_reach_x_without_fixed_topic_mapping(self):
        for query in ('stablecoin reserve transparency -filter:replies', '多模态机器人 泛化', 'bond yield & inflation from:researcher'):
            with self.subTest(query=query):
                transport = object.__new__(LocalHarnessXTransport)
                scripts = []
                def run(script):
                    scripts.append(script)
                    return {"status": "OBSERVED", "posts": [{"id": "1", "text": "Observed"}]}
                transport._run_jev_sync = run
                rows = asyncio.run(transport.discover(source="SEARCH", query=query))
                destination = json.loads(scripts[0].splitlines()[0].split(" = ", 1)[1])
                parsed = urlsplit(destination)
                self.assertEqual(parsed.hostname, "x.com")
                self.assertEqual(parse_qs(parsed.query)["q"], [query])
                self.assertEqual(parse_qs(parsed.query)["f"], ["live"])
                self.assertEqual(rows[0]["id"], "1")

    def test_search_without_query_fails_instead_of_substituting_a_preset(self):
        transport = object.__new__(LocalHarnessXTransport)
        transport._run_jev_sync = lambda script: self.fail("Empty search must not navigate")
        with self.assertRaises(ValueError):
            asyncio.run(transport.discover(source="SEARCH", query=" "))

    def test_domain_contract_accepts_all_three_directions_and_rejects_unrelated_domain(self):
        for domain in ("CRYPTOCURRENCY", "AI", "FINANCE"):
            self.assertEqual(XSearchPlan(domain=domain, query="a question", question="Why?", rationale="Worth investigating.").domain, domain)
        with self.assertRaises(ValidationError):
            XSearchPlan(domain="SPORTS", query="a question", question="Why?", rationale="Worth investigating.")

    def test_search_uses_durable_interests_open_questions_and_actual_prior_results(self):
        checkpoint = self.root / "application/content_mind/checkpoint.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text(json.dumps({"subject": {
            "interests": {"items": {"stablecoin-reserves": {"salience": 0.9, "preference": 0.8}}},
            "inquiry": {"inquiries": {"q1": {"question": "How are reserves verified?", "status": "OPEN"},
                                     "q2": {"question": "Old question", "status": "CLOSED"}}}
        }}))
        choice = {"domain": "CRYPTOCURRENCY", "query": "stablecoin reserve attestations",
                  "question": "How are reserves verified?", "rationale": "My open question needs new evidence."}
        runtime = SimpleNamespace(agent_manifest=None, cognitive_probe=AsyncMock(return_value=json.dumps(choice)))
        previous = {"decision": {"query": "stablecoin reserves"}, "post_count": 1,
                    "posts": [{"text": "An observed audit limitation", "url": "https://x.com/reader/status/1"}]}
        plan = asyncio.run(plan_x_search(runtime=runtime, state_root=self.root, folder=self.root / "cycle",
                                        mission={}, persona={}, previous=previous))
        self.assertEqual(plan.query, choice["query"])
        prompt = runtime.cognitive_probe.await_args.args[0]
        self.assertIn("stablecoin-reserves", prompt)
        self.assertIn("How are reserves verified?", prompt)
        self.assertIn("An observed audit limitation", prompt)
        self.assertNotIn("Old question", prompt)
        recovered = asyncio.run(plan_x_search(runtime=runtime, state_root=self.root, folder=self.root / "cycle",
                                             mission={}, persona={}, previous={}))
        self.assertEqual(recovered, plan)
        self.assertEqual(runtime.cognitive_probe.await_count, 1)

    def test_invalid_persisted_interest_state_is_visible(self):
        checkpoint = self.root / "application/content_mind/checkpoint.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text("{invalid")
        with self.assertRaises(json.JSONDecodeError):
            discovery_interests(self.root)

    def test_model_requests_memory_before_final_search_when_memory_exists(self):
        memory_db = self.root / "application/memory_system/memory.db"
        memory_db.parent.mkdir(parents=True)
        memory_db.touch()
        context_request = {
            "memory_queries": ["prior stablecoin reserve verification outcomes"]
        }
        choice = {
            "domain": "CRYPTOCURRENCY",
            "query": "stablecoin reserve proof audit",
            "question": "Which reserve verification methods expose the strongest evidence?",
            "rationale": "Use prior evidence gaps to choose a more discriminating public search.",
        }
        runtime = SimpleNamespace(
            agent_manifest=None,
            cognitive_probe=AsyncMock(
                side_effect=[json.dumps(context_request), json.dumps(choice)]
            ),
        )
        router = SimpleNamespace(recall=lambda query, limit: [{
            "memory_id": "mem-1",
            "memory_type": "SEMANTIC",
            "kind": "KNOWLEDGE",
            "text": "Prior research found attestations differ from independently verifiable reserves.",
            "confidence": 0.82,
            "importance": 0.9,
            "topics": ["stablecoin-reserves"],
            "entities": [],
            "evidence_refs": ["source:reserve-study"],
        }])
        with patch(
            "wechat_traffic_agent.memory_retrieval.MemoryRouter",
            return_value=router,
        ):
            plan = asyncio.run(plan_x_search(
                runtime=runtime,
                state_root=self.root,
                folder=self.root / "workspace",
                mission={},
                persona={},
                previous={},
            ))
        self.assertEqual(plan.query, choice["query"])
        self.assertEqual(runtime.cognitive_probe.await_count, 2)
        first_prompt = runtime.cognitive_probe.await_args_list[0].args[0]
        final_prompt = runtime.cognitive_probe.await_args_list[1].args[0]
        self.assertIn("X_SEARCH_CONTEXT_REQUEST", first_prompt)
        self.assertIn("prior stablecoin reserve verification outcomes", final_prompt)
        self.assertIn("independently verifiable reserves", final_prompt)

    def test_public_persona_preserves_owner_scope_before_identity_initialization(self):
        from wechat_traffic_agent.public_persona import public_persona_projection
        persona = public_persona_projection(self.root)
        self.assertEqual(persona["editorial_scope"]["domains"], ["CRYPTOCURRENCY", "AI", "FINANCE"])
        self.assertEqual(persona["editorial_scope"]["search_queries"], "AGENT_GENERATED")

    def test_zero_results_require_an_autonomous_new_query_before_browser_execution(self):
        original = {"domain": "CRYPTOCURRENCY", "query": "crypto drawdown regime clustering evidence",
                    "question": "Do drawdowns cluster?", "rationale": "A durable question deserves investigation."}
        revised = {**original, "query": "BTC volatility"}
        runtime = SimpleNamespace(agent_manifest=None,
                                  cognitive_probe=AsyncMock(side_effect=[json.dumps(original), json.dumps(revised)]))
        result = asyncio.run(plan_x_search(runtime=runtime, state_root=self.root, folder=self.root / "refine",
            mission={}, persona={}, previous={"decision": original, "post_count": 0, "posts": []}))
        self.assertEqual(result.query, revised["query"])
        self.assertEqual(runtime.cognitive_probe.await_count, 2)
        self.assertIn("previous query returned zero posts", runtime.cognitive_probe.await_args.args[0])
        self.assertIn("crypto drawdown regime clustering evidence", runtime.cognitive_probe.await_args.args[0])

    def test_repeating_an_empty_query_after_repair_is_an_explicit_failure(self):
        plan = {"domain": "FINANCE", "query": "bond inflation expectations",
                "question": "How are expectations measured?", "rationale": "The market mechanism deserves study."}
        runtime = SimpleNamespace(agent_manifest=None, cognitive_probe=AsyncMock(return_value=json.dumps(plan)))
        with self.assertRaisesRegex(ValueError, "previous query returned zero posts"):
            asyncio.run(plan_x_search(runtime=runtime, state_root=self.root, folder=self.root / "repeat",
                mission={}, persona={}, previous={"decision": plan, "post_count": 0, "posts": []}))
        self.assertEqual(runtime.cognitive_probe.await_count, 2)
        self.assertFalse((self.root / "repeat/search_plan.json").exists())
