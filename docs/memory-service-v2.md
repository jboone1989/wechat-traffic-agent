# Ferro memory service v2

Learning now reads bounded external memory through tools. Archives and exact
canonical references stay on the host; neither archive size nor the number of
source documents increases the prompt limit.

| Data | Authority and location | Retrieval view |
| --- | --- | --- |
| Beliefs, observations, application outcomes and relations | `application/memory_system/memory.db`: `memories`, append-only `memory_events` | FTS5, normalized trained vectors and contextual cards |
| Exact originals, with every revision retained | `application/memory_system/evidence/*.json`; `evidence_sources` selects the current original | Contiguous overlapping excerpts with exact original offsets |
| Contextual prefixes, topic summaries and navigation edges | `memory_nodes`, `memory_dependencies`; disposable derived views | Lexical + semantic recall, semantic summary hierarchy, entity/provenance expansion |
| Task-local reasoning state and reference bindings | Cycle folder `memory_session_<phase>.json` | Working summary, missing information, current cards and latest tool observation |
| Acquisition and learning decisions | Existing immutable cycle receipts and application precommit | Canonical observations preserve links to their receipt and originals |

`SEARCH_MEMORY`, `READ_MEMORY`, `READ_EVIDENCE`, `EXPAND_SUMMARY`,
`FOLLOW_RELATIONS`, and `GET_CHANGES` operate on short task-local references.
Search combines lexical and dense candidates with RRF, then a learned
cross-encoder reranks the bounded candidates. Filters for validity, status,
topic, node type and eligible source identities are enforced on the host.

The encoder defaults to normalized multilingual MiniLM-L12 embeddings. The
reranker defaults to a quantized Chinese/English BGE cross-encoder. Inference
runs in short-lived CPU workers and shares a disk model cache across releases.
Model failures are explicit; the learning service never substitutes feature
hashes for trained embeddings. Existing canonical-router deployments are
upgraded by creating `active_embedding.json`; legacy installations without
that marker retain their explicitly separate compatibility provider.

New original excerpts are staged. Lexical/structured lookup and cross-encoder
ranking can expose them for reading, but semantic indexing waits for an LLM
context prefix. Contextualization runs for a bounded set of selected/read
excerpts; remaining originals remain reachable by source expansion. Prefixes
never replace exact quotation text or change its evidence revision.

The summary hierarchy uses deterministic semantic clustering and bounded
fanout, with no more than six children per summary. Summaries preserve
qualifications and disagreements and serve navigation. Citation validation
always resolves original excerpts. Unchanged child/version groups reuse their
summary; replaced groupings retire old roots. This is a semantic summary tree,
not an implementation claim about the exact RAPTOR algorithm. Relationships
combine canonical entities, explicit extracted relations, and provenance
edges. Extracted relations are marked navigation hypotheses; this is not a
claim to implement GraphRAG's global community-report algorithm.

A source revision disputes dependent canonical observations and relations,
and dirties dependent summaries recursively. Canonical status changes
invalidate dependent views in the same transaction. Time expiry also
invalidates derived ancestors. Worldview confidence changes create successor
belief versions and supersede the earlier version. Historical receipts remain
auditable. Replaying an older receipt cannot roll back the current source.

All historical research receipts are imported incrementally, including records
outside the rolling compatibility ledger. Tested applications and their
corrections enter canonical memory. Precommit lineage excludes previously
seen sources and quotes from confirming a supposedly unseen case.

A model turn either requests a memory operation or commits a validated decision.
Tool results replace earlier results rather than accumulating forever. The
session persists each operation's observation and retains stable reference
bindings. Eight retrieval turns form one scheduler slice; exhaustion writes
`MEMORY_RETRIEVAL_PENDING`, does not write a terminal cycle result, and resumes
the same checkpoint. Final decisions retain host-side quote/reference bindings
so a crash before the next receipt does not change their meaning.

Every final serialized learning request, including schema and repair text, is
checked before dispatch. Defaults are 36,000 characters and an estimated
12,000 total tokens, including 2,400 reserved output tokens. `cl100k_base` is a
conservative planning estimate, not the browser model's exact tokenizer.
The selection window shrinks from eight cards to four, two or one against the
actual schema-inclusive request, with room reserved for a validation repair.
Original evidence and reference bindings stay intact; newly read evidence takes
priority. Models can retrieve the remaining cards in later turns.
`memory_request_sizes.jsonl` records actual serialized character counts.
Oversized requests fail explicitly; originals are never silently truncated.

Canonical FTS and vector maintenance consumes changes incrementally. Only
changed records are re-encoded; unchanged query embeddings and rerank results
are cached. `rebuild_views()` rebuilds disposable views from canonical records
and source pointers without deleting original evidence or event history.
The learning service maintains an incremental HNSW index with host-side filters.
Its binary generations are disposable; SQLite atomically selects the committed
generation. Updated vectors replace their labels and inactive records are
removed from active recall. The upgraded canonical router shares this ANN view and the rerank cache;
legacy installations retain their separate historical vector adapter.

Acceptance covers source revision propagation, canonical transitions, expiry,
view reconstruction, exact-quote invariance, incremental encoding, hierarchy
reuse and descent, iterative recovery, final request sizing, and tenfold archive
growth. Separate live ONNX checks exercise English paraphrases and Chinese
queries against English facts. Retrieval scores measure this small suite, not
general intelligence or guaranteed correctness.
