"""Deterministic retrieval doubles; real ONNX acceptance runs separately."""
import hashlib
import json
import math
import re

import pytest


class RetrievalModelsDouble:
    model_id = "explicit-test-embedding:v1"
    def __init__(self, root=None):
        self.encoded=[]
    def embed(self,texts,*,query=False):
        self.encoded.extend(texts)
        result=[]
        for text in texts:
            vector=[0.0]*64
            for term in re.findall(r"\w+",text.casefold()):
                vector[int(hashlib.sha256(term.encode()).hexdigest()[:8],16)%64]+=1
            scale=math.sqrt(sum(v*v for v in vector)) or 1
            result.append([v/scale for v in vector])
        return result
    def rerank(self,query,texts):
        wanted=set(re.findall(r"\w+",query.casefold()))
        return [len(wanted & set(re.findall(r"\w+",text.casefold()))) for text in texts]


@pytest.fixture(autouse=True)
def deterministic_memory_models(monkeypatch):
    from wechat_traffic_agent import memory_models
    monkeypatch.setattr(memory_models,"LocalRetrievalModels",RetrievalModelsDouble)


@pytest.fixture
def maintenance_cognition(monkeypatch):
    """Explicit cognition fixture for tests whose decision queues predate maintenance.

    Wrap the model double underneath BudgetedRuntime, so real budget checks run.
    """
    from agent_runtime.memory.session import BudgetedRuntime
    original=BudgetedRuntime.__init__
    class Cognition:
        def __init__(self,runtime): self.runtime=runtime
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            if task.startswith("LINK_MEMORY:"): return json.dumps({"relations":[]})
            if task.startswith("CONSOLIDATE_MEMORY:"):
                return json.dumps({"summary":"Evidence records retain unresolved details and qualified observations.","context_prefix":"Research observations within the selected topic."})
            if task.startswith("CONTEXTUALIZE_MEMORY:"):
                data=json.JSONDecoder().raw_decode(task.split("\n",1)[1])[0]
                return json.dumps({"prefixes":[{"ref":r["ref"],"prefix":"This excerpt concerns its identified source and research question."} for r in data["chunks"]]})
            if output_schema is None:
                return await self.runtime.cognitive_probe(task,max_tokens=max_tokens)
            return await self.runtime.cognitive_probe(task,max_tokens=max_tokens,output_schema=output_schema)
    def initialize(self,runtime,folder,budget=None,**kwargs):
        original(self,Cognition(runtime),folder,budget,**kwargs)
    monkeypatch.setattr(BudgetedRuntime,"__init__",initialize)
