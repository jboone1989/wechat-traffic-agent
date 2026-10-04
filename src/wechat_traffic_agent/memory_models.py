"""CPU semantic retrieval with explicit models; no feature-hash fallback.

Inference runs in a short-lived worker so the browser agent does not retain ONNX
weights. The disk cache is shared by releases; documents are encoded only when
their version or embedding model changes.
"""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import resource
import time
from uuid import uuid4


class LocalRetrievalModels:
    def __init__(self, root):
        self.embedding = os.getenv("FERRO_MEMORY_EMBEDDING", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        self.reranker = os.getenv("FERRO_MEMORY_RERANKER", "ferro/bge-reranker-base-int8")
        self.model_id = self.embedding + ":normalized:v1"
        self.cache = os.getenv("FERRO_MEMORY_MODEL_CACHE", str(Path.home() / ".cache" / "ferro-memory-models"))

    def _run(self, operation, texts, query=""):
        request = dict(operation=operation, texts=texts, query=query,
                       embedding=self.embedding, reranker=self.reranker, cache=self.cache)
        # One host inference at a time, in a transient unit owned by the user
        # manager. One-GiB resident limit is explicit; model inference cannot
        # consume host swap. All documents are still scored, one at a time.
        cache=Path(self.cache)
        cache.mkdir(parents=True,exist_ok=True)
        environment=dict(os.environ)
        user_runtime='/run/user/'+str(os.getuid())
        environment.setdefault('XDG_RUNTIME_DIR',user_runtime)
        environment.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+user_runtime+'/bus')
        with (cache/'inference.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            process = subprocess.run([
                'systemd-run','--user','--quiet','--pipe','--wait','--collect',
                '--property=RuntimeMaxSec=240',
                '--property=MemoryMax=1G','--property=MemorySwapMax=0',
                '--unit=ferro-memory-'+uuid4().hex,
                sys.executable,str(Path(__file__).resolve())],
                input=json.dumps(request),text=True,capture_output=True,timeout=255,env=environment)
        if process.returncode:
            raise RuntimeError("MEMORY_MODEL_FAILED: " + process.stderr[-1600:])
        return json.loads(process.stdout)

    def embed(self, texts, *, query=False):
        return self._run("query" if query else "embed", texts)

    def rerank(self, query, texts):
        return self._run("rerank", texts, query)


def worker():
    import numpy as np
    started = time.monotonic()
    request = json.load(sys.stdin)
    if request["operation"] == "rerank":
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        if request["reranker"]=="ferro/bge-reranker-base-int8":
            from fastembed.common.model_description import ModelSource
            TextCrossEncoder.add_custom_model("ferro/bge-reranker-base-int8",
                sources=ModelSource(hf="Xenova/bge-reranker-base"),
                model_file="onnx/model_quantized.onnx",license="mit",size_in_gb=0.28,
                description="Quantized BGE bilingual Chinese/English cross-encoder")
        model = TextCrossEncoder(request["reranker"], cache_dir=request["cache"], threads=2, local_files_only=False,
                                 enable_cpu_mem_arena=False)
        result = [float(value) for value in model.rerank(request["query"], request["texts"], batch_size=1)]
    else:
        from fastembed import TextEmbedding
        model = TextEmbedding(request["embedding"], cache_dir=request["cache"], threads=2, local_files_only=False)
        vectors = (model.query_embed(request["texts"], batch_size=1) if request["operation"] == "query"
                   else model.embed(request["texts"], batch_size=1))
        result = [(vector / max(float(np.linalg.norm(vector)), 1e-12)).tolist() for vector in vectors]
    metrics = {"operation": request["operation"], "documents": len(request["texts"]),
               "elapsed_seconds": round(time.monotonic() - started, 3),
               "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               "completed_at": time.time(), "pid": os.getpid()}
    with (Path(request["cache"]) / "inference-metrics.jsonl").open("a") as output:
        output.write(json.dumps(metrics) + "\n")
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    worker()
