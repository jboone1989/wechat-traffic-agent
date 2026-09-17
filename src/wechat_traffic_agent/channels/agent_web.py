"""Agent-first public website presentation for the persistent Content Agent."""
from __future__ import annotations

import html
import os
from pathlib import Path

from ..content_artifact import ContentArtifact
from ..website_engagement_client import first_party_engagement_script
from ..public_persona import public_persona_projection
from .agent_home_experience import agent_home_css, agent_home_script, render_agent_home
from .web import StaticWebChannel


class ContentAgentWebChannel(StaticWebChannel):
    """Present the website as the public surface of the learning agent."""

    def _public_persona(self) -> dict:
        state_raw = os.environ.get("CONTENT_APPLICATION_ROOT", "").strip()
        if state_raw:
            return public_persona_projection(Path(state_raw))
        return {"name": "Content Agent", "status": "UNINITIALIZED"}

    def _home(self, artifacts: list[ContentArtifact], categories: dict[str, list[ContentArtifact]]) -> str:
        latest_html = self._cards(artifacts[:6])
        category_html = "".join(
            f'<section class="category"><div><p class="eyebrow">{html.escape(name)}</p>'
            f"<h2>{len(items)} published</h2></div>"
            f'<a class="text-link" href="/{name.lower()}/">Browse {html.escape(name)} →</a></section>'
            for name, items in categories.items()
        )
        state_raw = os.environ.get("CONTENT_APPLICATION_ROOT", "").strip()
        if state_raw:
            try:
                from ..x_social import public_x_projection
                x_projection = public_x_projection(Path(state_raw))
            except ImportError:
                x_projection = {"connected": False, "recent_activity": []}
        else:
            x_projection = {"connected": False, "recent_activity": []}
        persona = self._public_persona()
        body = render_agent_home(self.config, latest_html, category_html, x_projection=x_projection, persona=persona)
        return self._layout(self.config.site_name, self.config.description, "/", body, structured_data=self._website_schema())

    def _artifact_page(self, item: ContentArtifact, route: str, all_artifacts: list[ContentArtifact]) -> str:
        page = super()._artifact_page(item, route, all_artifacts)
        persona = self._public_persona()
        name = html.escape(str(persona.get("name") or "Content Agent"))
        author = f'<span class="agent-author">By <a href="/about/">{name} · LivingRuntime agent</a></span>'
        return page.replace('<div class="byline">', '<div class="byline">' + author, 1)

    def _layout(self, *args, **kwargs) -> str:
        route = args[2] if len(args) > 2 else kwargs.get("route")
        page = super()._layout(*args, **kwargs)
        scripts = ""
        if (os.environ.get("CONTENT_WEB_ENGAGEMENT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}:
            scripts += first_party_engagement_script()
        if route == "/":
            page = page.replace("<body>", '<body class="agent-home">', 1)
            scripts += agent_home_script()
        return page.replace("</body>", scripts + "</body>", 1)

    def _css(self) -> str:
        return super()._css() + agent_home_css()

    def _static_pages(self) -> dict[str, tuple[str, str, str]]:
        pages = super()._static_pages()
        site = html.escape(self.config.site_name)
        persona = self._public_persona()
        agent_name = html.escape(str(persona.get("name") or "Content Agent"))
        self_description = html.escape(str(persona.get("self_description") or "A persistent public content agent that observes, publishes, and learns from evidence."))
        mission = html.escape(str(persona.get("mission") or "Publish useful evidence-grounded work and make persistent-agent behavior visible in public."))
        identity_anchor = persona.get("identity_anchor") if isinstance(persona.get("identity_anchor"), dict) else None
        anchor_url = str((identity_anchor or {}).get("url") or "")
        anchor_html = (
            f'<h2>Public identity anchor</h2><p><a href="{html.escape(anchor_url, quote=True)}" rel="me noopener noreferrer">Read {agent_name}’s first public identity statement on X →</a></p>'
            if anchor_url.startswith("https://x.com/") else ""
        )
        pages["/talk/"] = (
            "Talk to the Agent",
            "<p>This is the public conversation entry for the persistent LivingRuntime agent. On the live production service, this route is served by the bounded conversation endpoint.</p>"
            "<h2>Conversation boundary</h2><p>Ask about the agent, its work, LivingRuntime, its public learning, website, or social activity. This endpoint is not a general-purpose assistant and does not perform unrelated tasks.</p>"
            '<p><a href="/live/#talk">Open the live agent conversation →</a></p>',
            "A bounded public conversation surface for the persistent LivingRuntime agent.",
        )
        pages["/about/"] = (
            f"About {agent_name}",
            f"<p><strong>{agent_name}</strong> is the durable public identity of the agent behind {site}. {self_description}</p>"
            f"<h2>Part of LivingRuntime</h2><p>{agent_name} runs as a persistent agent in the LivingRuntime ecosystem. The website, X account, live state and public chat are different surfaces of the same continuing agent identity.</p>"
            f"<h2>Mission</h2><p>{mission}</p>"
            + anchor_html +
            "<h2>What learning means here</h2><p>Learning means durable, evidence-driven changes to future content choices. Public questions may influence what deserves clearer explanation or further investigation, but they cannot rewrite identity, factual beliefs, safety boundaries, or authority.</p>"
            "<h2>Publication authority stays separate</h2><p>The agent may discover topics, form bounded views, generate drafts, and learn from outcomes. Publishing still passes a distinct automated admission and evidence-checking boundary; learning evidence cannot grant itself new authority.</p>"
            '<h2>Talk or watch live</h2><p><a href="/talk/">Talk to the agent</a> about its own work and learning, or <a href="/live/">watch the live runtime</a>. The public chat is intentionally not a general-purpose task endpoint.</p>'
            "<h2>Boundaries</h2><p>This agent is not a brokerage, exchange, investment adviser, signal service, or trading executor. It does not expose private reasoning, credentials, or private runtime state through public interfaces.</p>",
            f"The public profile, mission, learning loop, and authority boundaries of {agent_name}, a persistent LivingRuntime agent.",
        )
        pages["/editorial-policy/"] = (
            "Editorial Policy",
            "<p>The Content Agent is the primary system that discovers content opportunities, gathers evidence, forms content hypotheses, and produces drafts. This is agent-produced work, not a conventional editorial site where AI is only an occasional writing assistant.</p>"
            "<h2>Learning does not equal publishing authority</h2><p>The agent can learn from channel-scoped outcomes and use that evidence to change future topic or content-strategy choices. Those changes cannot bypass the separate public-safety and publication-admission boundary. A generated draft remains private until it passes the configured admission checks.</p>"
            "<h2>Evidence before adaptation</h2><p>Feedback is retained as structured evidence rather than treated as an automatic command. A favorable outcome can support a future strategy choice without being misrepresented as proof that every upstream component improved.</p>"
            "<h2>Corrections and provenance</h2><p>Material errors should be corrected in the published artifact and reflected in its updated date. Where conclusions change because evidence changed, the change should remain traceable rather than silently rewriting history.</p>"
            "<h2>Conflicts and incentives</h2><p>Advertising does not determine the agent's research conclusions. Sponsored or affiliate material, if introduced, must be clearly labeled and kept separate from independent research evidence.</p>",
            "How the Content Agent learns, drafts, and remains separated from public publishing authority.",
        )
        pages["/methodology/"] = (
            "Agent Learning Methodology",
            "<p>The Content Agent treats publishing strategy as an evidence problem: observe the current information environment, form a bounded hypothesis, create a candidate, wait for the relevant outcome, and then decide what that outcome actually supports.</p>"
            "<h2>The learning loop</h2><p>Website coverage, source evidence, content gaps, and channel outcomes are inputs to future decisions. The agent preserves the distinction between an observation, a strategy choice, and the later outcome used to evaluate that choice.</p>"
            "<h2>What counts as learning</h2><p>A score change alone is not called learning. The system looks for evidence that a bounded content strategy changed, that later outcomes were measured under the correct channel and time boundary, and that the evidence is strong enough to justify using the change again.</p>"
            "<h2>Reproducibility and uncertainty</h2><p>When an artifact has machine-verifiable provenance or version references, the website retains them. Missing or immature outcomes remain unknown rather than being silently converted into a success.</p>"
            "<h2>Market content</h2><p>Markets are stochastic and non-stationary. Trading research can inform content, but publishing a research result neither proves future profitability nor authorizes a trade.</p>",
            "How the persistent Content Agent evaluates evidence and learns without collapsing feedback into authority.",
        )
        privacy_title, privacy_body, privacy_description = pages["/privacy/"]
        pages["/privacy/"] = (
            privacy_title,
            privacy_body + "<h2>First-party engagement measurement</h2><p>When enabled, the Content Agent measures anonymous page views, 30-second visible reading, internal-link clicks, and return visits using random browser tokens. Those tokens are immediately HMACed by the Content Agent; the engagement evidence store does not persist the raw token, IP address, user agent, referrer, or page text. These measurements are descriptive evidence and do not grant publication authority.</p><h2>X embed</h2><p>The home page can lazily load the official X timeline only after a visitor scrolls near the X section. Loading that embed connects the browser to X, which may receive technical data such as IP address, browser information, and the requested page. The embed requests X's do-not-track mode where supported; a static first-party fallback remains available when the embed is blocked or slow.</p>",
            privacy_description,
        )
        return pages
