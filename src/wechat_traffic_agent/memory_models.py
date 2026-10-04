"""Compatibility import for the reusable local memory model provider."""
from agent_runtime.memory.local_retrieval import LocalRetrievalModels, worker

__all__ = ["LocalRetrievalModels", "worker"]


if __name__ == "__main__":
    worker()
