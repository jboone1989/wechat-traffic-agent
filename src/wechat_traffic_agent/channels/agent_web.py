"""Agent-first public website presentation for the persistent Content Agent."""
from __future__ import annotations

import html
import os
from pathlib import Path

from ..content_artifact import ContentArtifact
from ..website_engagement_client import first_party_engagement_script
from ..public_persona import public_persona_projection
from .agent_home_experience import agent_home_css, agent_home_script, render_agent_home
from .agent_work_library import work_library_css, work_library_script
from .web import StaticWebChannel, _route_for, _tag_slug


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
        persona = self._public_persona()
        body = render_agent_home(self.config, latest_html, category_html, persona=persona)
        return self._layout(self.config.site_name, self.config.description, "/", body, structured_data=self._website_schema())

    def _header(self) -> str:
        persona = self._public_persona()
        name = html.escape(str(persona.get("name") or "Content Agent"))
        return (
            '<header class="site-header"><div class="shell nav-wrap">'
            f'<a class="brand" href="/">{name}<span class="brand-ecosystem"> / LivingRuntime</span></a>'
            '<nav aria-label="Primary"><a href="/about/">Ferro</a><a href="/live/">Live</a>'
            '<a href="/talk/">Talk</a><a href="/archive/">Work</a><a href="https://x.com/caini56006962" rel="me noopener noreferrer">X ↗</a>'
            '<a href="https://network.livingruntime.com/explorer" rel="noopener noreferrer">Network ↗</a></nav>'
            '</div></header>'
        )

    def _archive(self, artifacts) -> str:
        ordered = sorted(artifacts, key=lambda item: item.created_at, reverse=True)
        topic_map: dict[str, tuple[str, int]] = {}
        for item in ordered:
            for tag in item.tags:
                slug = _tag_slug(tag)
                if not slug:
                    continue
                label, count = topic_map.get(slug, (tag.strip(), 0))
                topic_map[slug] = (label, count + 1)
        kind_counts: dict[str, int] = {}
        for item in ordered:
            key = item.kind.value
            kind_counts[key] = kind_counts.get(key, 0) + 1
        kind_buttons = ['<button type="button" class="work-kind active" data-kind="all">All <span>%d</span></button>' % len(ordered)]
        for key in ("article", "research", "guide", "note"):
            if kind_counts.get(key):
                kind_buttons.append(
                    f'<button type="button" class="work-kind" data-kind="{key}">{key.title()} <span>{kind_counts[key]}</span></button>'
                )
        topic_options = ['<option value="all">All topics</option>']
        for slug, (label, count) in sorted(topic_map.items(), key=lambda row: (-row[1][1], row[1][0].lower())):
            topic_options.append(f'<option value="{html.escape(slug, quote=True)}">{html.escape(label)} ({count})</option>')
        cards = []
        for index, item in enumerate(ordered):
            tags = [(tag, _tag_slug(tag)) for tag in item.tags if _tag_slug(tag)]
            tag_links = ''.join(
                f'<button type="button" class="work-tag" data-pick-topic="{html.escape(slug, quote=True)}">{html.escape(tag)}</button>'
                for tag, slug in tags[:4]
            )
            if len(tags) > 4:
                tag_links += f'<span class="work-tag-more">+{len(tags)-4}</span>'
            search_text = " ".join([item.title, item.summary, item.kind.value, *item.tags]).casefold()
            topic_slugs = " ".join(slug for _, slug in tags)
            date_iso = item.created_at.isoformat()
            date_label = item.created_at.strftime("%b %d, %Y")
            route = _route_for(item)
            cards.append(
                f'<article class="work-item" data-work-item data-kind="{html.escape(item.kind.value, quote=True)}" '
                f'data-topics="{html.escape(topic_slugs, quote=True)}" data-search="{html.escape(search_text, quote=True)}" '
                f'data-date="{html.escape(date_iso, quote=True)}" data-index="{index}">'
                f'<div class="work-item-meta"><span class="work-type">{html.escape(item.kind.value.upper())}</span>'
                f'<time datetime="{html.escape(date_iso, quote=True)}">{html.escape(date_label)}</time></div>'
                f'<h2><a href="{html.escape(route, quote=True)}">{html.escape(item.title)}</a></h2>'
                f'<p>{html.escape(item.summary)}</p><div class="work-tags">{tag_links}</div>'
                f'<a class="work-open" href="{html.escape(route, quote=True)}">Open work <span>→</span></a></article>'
            )
        body = (
            '<section class="work-library" data-work-library>'
            '<header class="work-hero"><p class="eyebrow">FERRO · WORK</p><h1>Research you can actually find again.</h1>'
            f'<p>{len(ordered)} published pieces. Search by idea, filter by format or topic, and sort the archive without leaving the page.</p></header>'
            '<div class="work-search-sticky"><label class="work-search"><span>Search</span>'
            '<input id="work-search" type="search" autocomplete="off" placeholder="Search titles, summaries, topics…"></label></div>'
            '<div class="work-controls" aria-label="Work filters"><label class="work-select"><span>Topic</span><select id="work-topic">' + ''.join(topic_options) + '</select></label>'
            '<label class="work-select work-sort"><span>Sort</span><select id="work-sort"><option value="newest">Newest first</option>'
            '<option value="oldest">Oldest first</option></select></label></div>'
            '<div class="work-kind-row" id="work-kinds" aria-label="Content type">' + ''.join(kind_buttons) + '</div>'
            + f'<div class="work-result-bar"><strong id="work-count">{len(ordered)} results</strong><button type="button" id="work-clear">Clear filters</button></div>'
            + '<div class="work-grid" id="work-grid">' + ''.join(cards) + '</div>'
            '<div class="work-empty" id="work-empty" hidden><strong>No matching work.</strong><p>Try a broader search or clear one of the filters.</p></div>'
            '<noscript><p class="work-noscript">Search and filters require JavaScript; all published work is still listed above.</p></noscript></section>'
        )
        return self._layout("Work", "Search and browse Ferro's published work.", "/archive/", body)

    def _footer(self) -> str:
        persona = self._public_persona()
        name = html.escape(str(persona.get("name") or "Content Agent"))
        return (
            '<footer class="site-footer"><div class="shell footer-grid"><div>'
            f'<strong>{name} · LivingRuntime</strong><p>Persistent AI agent. Public work, learning, and conversation.</p></div>'
            '<nav aria-label="Footer"><a href="/archive/">Work</a><a href="/methodology/">Methodology</a>'
            '<a href="/editorial-policy/">Editorial policy</a><a href="/privacy/">Privacy</a><a href="/contact/">Contact</a></nav></div>'
            f'<div class="shell copyright">{name} is part of the LivingRuntime ecosystem. Market content is not investment advice.</div></footer>'
        )

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
        elif route == "/archive/":
            page = page.replace("<body>", '<body class="work-page">', 1)
            scripts += work_library_script()
        return page.replace("</body>", scripts + "</body>", 1)

    def _css(self) -> str:
        return super()._css() + agent_home_css() + work_library_css()

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
            privacy_body + "<h2>First-party engagement measurement</h2><p>When enabled, the Content Agent measures anonymous page views, 30-second visible reading, internal-link clicks, and return visits using random browser tokens. Those tokens are immediately HMACed by the Content Agent; the engagement evidence store does not persist the raw token, IP address, user agent, referrer, or page text. These measurements are descriptive evidence and do not grant publication authority.</p>",
            privacy_description,
        )
        return pages
