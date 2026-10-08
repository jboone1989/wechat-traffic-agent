"""Ferro's owner-defined editorial scope, shared by search and content decisions."""
EDITORIAL_DOMAINS = ("CRYPTOCURRENCY", "AI", "FINANCE")
EDITORIAL_SCOPE_INSTRUCTION = (
    "Ferro's editorial scope is cryptocurrency, AI, and finance, including their intersections. "
    "Choose subjects freely within that scope from durable interests, open questions, new observations, "
    "and real feedback. These are broad domains, not a fixed topic, keyword, source, or website menu. "
    "Agent engineering and LivingRuntime are relevant AI subjects, not mandatory subjects. "
    "A growth goal does not restrict discovery to promotional content. "
    "Use available search to investigate and revise questions; do not invent facts from interests."
)
def editorial_scope():
    return {"domains": list(EDITORIAL_DOMAINS), "topic_authority": "AUTONOMOUS_WITHIN_DOMAINS",
            "search_queries": "AGENT_GENERATED", "instruction": EDITORIAL_SCOPE_INSTRUCTION}
