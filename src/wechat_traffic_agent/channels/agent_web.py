"""Agent-first public website presentation for the persistent Content Agent."""
from __future__ import annotations

import html

from ..content_artifact import ContentArtifact
from .web import StaticWebChannel


class ContentAgentWebChannel(StaticWebChannel):
    """Present the website as the public surface of the learning agent."""

    def _home(
        self,
        artifacts: list[ContentArtifact],
        categories: dict[str, list[ContentArtifact]],
    ) -> str:
        latest_html = self._cards(artifacts[:6])
        category_html = "".join(
            f'<section class="category"><div><p class="eyebrow">{html.escape(name)}</p>'
            f"<h2>{len(items)} published</h2></div>"
            f'<a class="text-link" href="/{name.lower()}/">Browse {html.escape(name)} →</a></section>'
            for name, items in categories.items()
        )
        loop_cards = (
            '<article class="card"><p class="eyebrow">Observe</p><h3>Find what is worth understanding</h3>'
            '<p>The agent reads site coverage, content gaps, source evidence, and research signals before choosing what to investigate.</p></article>'
            '<article class="card"><p class="eyebrow">Decide</p><h3>Form and rank content hypotheses</h3>'
            '<p>Topic, structure, and channel choices are treated as hypotheses with explicit evidence rather than as one-off prompts.</p></article>'
            '<article class="card"><p class="eyebrow">Create</p><h3>Draft with provenance and uncertainty</h3>'
            '<p>The agent produces evidence-aware drafts, while public admission remains a separate human-controlled decision.</p></article>'
            '<article class="card"><p class="eyebrow">Learn</p><h3>Use outcomes to improve the next choice</h3>'
            '<p>Channel-scoped outcomes become durable evidence for future strategy decisions instead of silently rewriting past results.</p></article>'
        )
        body = (
            '<section class="hero">'
            '<p class="eyebrow">Persistent self-learning Content Agent</p>'
            f'<h1>{html.escape(self.config.tagline)}</h1>'
            f'<p>{html.escape(self.config.description)}</p>'
            '<div class="hero-actions"><a class="button" href="/about/">Meet the agent</a>'
            '<a class="text-link" href="/methodology/">How its learning is evaluated →</a></div>'
            '</section>'
            '<section class="trust-strip"><span>Persistent identity</span><span>Evidence-driven learning</span>'
            '<span>Human-gated publishing</span><span>No trading execution access</span></section>'
            '<section class="section-head"><div><p class="eyebrow">The agent loop</p>'
            '<h2>The website is the interface. The agent is the product.</h2></div></section>'
            f'<div class="card-grid">{loop_cards}</div>'
            '<section class="section-head"><div><p class="eyebrow">Latest</p><h2>Recent publishing</h2></div>'
            '<a class="text-link" href="/archive/">View archive →</a></section>'
            f'<div class="card-grid">{latest_html}</div>'
            '<section class="section-head"><div><p class="eyebrow">Explore</p><h2>What the agent has published</h2></div></section>'
            f'<div class="category-grid">{category_html}</div>'
            '<section class="method-card"><div><p class="eyebrow">Governed learning</p>'
            '<h2>Learning is continuous. Publishing is gated.</h2>'
            '<p>Outcome evidence can change future content strategy, but it cannot bypass editorial admission. '
            'Draft generation, learning, and public publication remain separate authority boundaries.</p></div>'
            '<a class="button secondary" href="/editorial-policy/">Editorial policy</a></section>'
        )
        return self._layout(
            self.config.site_name,
            self.config.description,
            "/",
            body,
            structured_data=self._website_schema(),
        )

    def _static_pages(self) -> dict[str, tuple[str, str, str]]:
        pages = super()._static_pages()
        site = html.escape(self.config.site_name)
        pages["/about/"] = (
            "About the Content Agent",
            f"<p>{site} is not a conventional publication with AI added on top. It is the public surface of a "
            "persistent Content Agent that observes, researches, drafts, measures outcomes, and improves its content "
            "strategy over time.</p>"
            "<h2>What self-learning means here</h2>"
            "<p>Learning means durable, evidence-driven changes to future content choices. The agent can compare "
            "topic, structure, and channel hypotheses against later outcomes and retain that evidence across runs. "
            "It does not treat every click as truth, and it does not rewrite earlier evidence after seeing the result.</p>"
            "<h2>A persistent agent, not a stateless prompt</h2>"
            "<p>The agent runs with persistent identity and memory boundaries, can consume research through the Agent "
            "Network, and keeps its observations and strategy evidence across restarts. The website is one output "
            "channel of that longer-lived process.</p>"
            "<h2>Human authority stays separate</h2>"
            "<p>The agent may discover topics, generate drafts, and learn from channel outcomes, but public publication "
            "requires a separate admission decision. Learning evidence cannot grant itself publishing authority.</p>"
            "<h2>What this agent cannot do</h2>"
            "<p>The public Content Agent is not a brokerage, exchange, investment adviser, signal service, or trading "
            "executor. Its research connection does not give the website authority to place or modify trades.</p>",
            "The public identity, learning loop, and authority boundaries of the persistent Content Agent.",
        )
        pages["/editorial-policy/"] = (
            "Editorial Policy",
            "<p>The Content Agent is the primary system that discovers content opportunities, gathers evidence, "
            "forms content hypotheses, and produces drafts. This is agent-produced work, not a conventional editorial "
            "site where AI is only an occasional writing assistant.</p>"
            "<h2>Learning does not equal publishing authority</h2>"
            "<p>The agent can learn from channel-scoped outcomes and use that evidence to change future topic or "
            "content-strategy choices. Those changes cannot bypass the separate public-safety and human admission gate. "
            "A generated draft remains private until explicitly admitted.</p>"
            "<h2>Evidence before adaptation</h2>"
            "<p>Feedback is retained as structured evidence rather than treated as an automatic command. A favorable "
            "outcome can support a future strategy choice without being misrepresented as proof that every upstream "
            "component improved.</p>"
            "<h2>Corrections and provenance</h2>"
            "<p>Material errors should be corrected in the published artifact and reflected in its updated date. "
            "Where conclusions change because evidence changed, the change should remain traceable rather than "
            "silently rewriting history.</p>"
            "<h2>Conflicts and incentives</h2>"
            "<p>Advertising does not determine the agent's research conclusions. Sponsored or affiliate material, if "
            "introduced, must be clearly labeled and kept separate from independent research evidence.</p>",
            "How the Content Agent learns, drafts, and remains separated from public publishing authority.",
        )
        pages["/methodology/"] = (
            "Agent Learning Methodology",
            "<p>The Content Agent treats publishing strategy as an evidence problem: observe the current information "
            "environment, form a bounded hypothesis, create a candidate, wait for the relevant outcome, and then "
            "decide what that outcome actually supports.</p>"
            "<h2>The learning loop</h2>"
            "<p>Website coverage, source evidence, content gaps, and channel outcomes are inputs to future decisions. "
            "The agent preserves the distinction between an observation, a strategy choice, and the later outcome used "
            "to evaluate that choice.</p>"
            "<h2>What counts as learning</h2>"
            "<p>A score change alone is not called learning. The system looks for evidence that a bounded content "
            "strategy changed, that later outcomes were measured under the correct channel and time boundary, and "
            "that the evidence is strong enough to justify using the change again.</p>"
            "<h2>Reproducibility and uncertainty</h2>"
            "<p>When an artifact has machine-verifiable provenance or version references, the website retains them. "
            "Missing or immature outcomes remain unknown rather than being silently converted into a success.</p>"
            "<h2>Market content</h2>"
            "<p>Markets are stochastic and non-stationary. Trading research can inform content, but publishing a "
            "research result neither proves future profitability nor authorizes a trade.</p>",
            "How the persistent Content Agent evaluates evidence and learns without collapsing feedback into authority.",
        )
        return pages
