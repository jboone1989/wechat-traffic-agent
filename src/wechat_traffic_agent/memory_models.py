"""CPU semantic retrieval with explicit models; no feature-hash fallback.

Inference runs in a short-lived worker so the browser agent does not retain ONNX
weights. The disk cache is shared by releases; documents are encoded only when
their version or embedding model changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


class LocalRetrievalModels:
    def __init__(self, root):
        self.embedding = os.getenv("FERRO_MEMORY_EMBEDDING", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        self.reranker = os.getenv("FERRO_MEMORY_RERANKER", "ferro/bge-reranker-base-int8")
        self.model_id = self.embedding + ":normalized:v1"
        self.cache = os.getenv("FERRO_MEMORY_MODEL_CACHE", str(Path.home() / ".cache" / "ferro-memory-models"))

    def _run(self, operation, texts, query=""):
        request = dict(operation=operation, texts=texts, query=query,
                       embedding=self.embedding, reranker=self.reranker, cache=self.cache)
        process = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                 input=json.dumps(request), text=True, capture_output=True, timeout=240)
        if process.returncode:
            raise RuntimeError("MEMORY_MODEL_FAILED: " + process.stderr[-1600:])
        return json.loads(process.stdout)

    def embed(self, texts, *, query=False):
        return self._run("query" if query else "embed", texts)

    def rerank(self, query, texts):
        return self._run("rerank", texts, query)


def worker():
    import numpy as np
    request = json.load(sys.stdin)
    if request["operation"] == "rerank":
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        if request["reranker"]=="ferro/bge-reranker-base-int8":
            from fastembed.common.model_description import ModelSource
            TextCrossEncoder.add_custom_model("ferro/bge-reranker-base-int8",
                sources=ModelSource(hf="Xenova/bge-reranker-base"),
                model_file="onnx/model_quantized.onnx",license="mit",size_in_gb=0.28,
                description="Quantized BGE bilingual Chinese/English cross-encoder")
        model = TextCrossEncoder(request["reranker"], cache_dir=request["cache"], threads=2)
        result = [float(value) for value in model.rerank(request["query"], request["texts"])]
    else:
        from fastembed import TextEmbedding
        model = TextEmbedding(request["embedding"], cache_dir=request["cache"], threads=2)
        vectors = (model.query_embed(request["texts"]) if request["operation"] == "query"
                   else model.embed(request["texts"], batch_size=32))
        result = [(vector / max(float(np.linalg.norm(vector)), 1e-12)).tolist() for vector in vectors]
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    worker()
