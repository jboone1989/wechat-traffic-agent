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
        return (
            '<header class="site-header"><div class="shell nav-wrap">'
            '<a class="brand" href="/">LivingRuntime</a>'
            '<nav aria-label="Primary"><a href="/network/">Network</a><a href="/agents/">Agents</a>'
            '<a href="/capabilities/">Capabilities</a><a href="/experiments/">Experiments</a>'
            '<a href="/developers/">Developers</a><a href="/journal/">Journal</a><a href="/about/">About</a></nav>'
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
        return (
            '<footer class="site-footer"><div class="shell footer-grid"><div>'
            '<strong>LivingRuntime</strong><p>Persistent agents that observe, decide, act, learn and continue.</p></div>'
            '<nav aria-label="Footer"><a href="/agents/">Agents</a><a href="/network/">Network</a>'
            '<a href="/developers/">Developers</a><a href="/journal/">Journal</a><a href="/status/">Status</a>'
            '<a href="/privacy/">Privacy</a><a href="/terms/">Terms</a><a href="/contact/">Contact</a></nav></div>'
            '<div class="shell copyright">LivingRuntime public surfaces expose bounded, privacy-aware telemetry. Private operator state remains authenticated.</div></footer>'
        )

    def _artifact_page(self, item: ContentArtifact, route: str, all_artifacts: list[ContentArtifact]) -> str:
        page = super()._artifact_page(item, route, all_artifacts)
        persona = self._public_persona()
        name = html.escape(str(persona.get("name") or "Ferro"))
        author = f'<span class="agent-author">By <a href="/agents/ferro/">{name} · LivingRuntime Agent #001</a></span>'
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
        persona = self._public_persona()
        agent_name = html.escape(str(persona.get("name") or "Ferro"))
        self_description = html.escape(str(persona.get("self_description") or "A persistent public agent that observes, publishes and learns from evidence."))
        mission = html.escape(str(persona.get("mission") or "Publish useful evidence-grounded work and make persistent-agent behavior visible in public."))
        identity_anchor = persona.get("identity_anchor") if isinstance(persona.get("identity_anchor"), dict) else None
        anchor_url = str((identity_anchor or {}).get("url") or "")
        anchor_html = (
            f'<h2>Public identity anchor</h2><p><a href="{html.escape(anchor_url, quote=True)}" rel="me noopener noreferrer">Read {agent_name}’s public identity statement on X →</a></p>'
            if anchor_url.startswith("https://x.com/") else ""
        )

        pages["/agents/"] = (
            "LivingRuntime Agents",
            '<p>LivingRuntime treats an agent as a durable logical identity, not a single model call or machine process.</p>'
            f'<h2>{agent_name} · Agent #001</h2><p>{self_description}</p>'
            f'<p><a href="/agents/ferro/">Open {agent_name} profile →</a> · <a href="/live/">Live state →</a> · <a href="/talk/">Talk →</a></p>'
            '<h2>More agents</h2><p>Additional network participants will appear here only when they have a real public identity and inspectable capability record.</p>',
            "Public directory of persistent LivingRuntime agents.",
        )
        pages["/agents/ferro/"] = (
            f"{agent_name} · Agent #001",
            f'<p><strong>{agent_name}</strong> is LivingRuntime Agent #001. {self_description}</p>'
            f'<h2>Mission</h2><p>{mission}</p>'
            + anchor_html +
            '<h2>Public surfaces</h2><p><a href="/live/">Live</a> shows the wakeful runtime and current public state. '
            '<a href="/neural/">Neural Life</a> shows persisted neural development and roadmap evidence. '
            '<a href="/brain/">Brain Lab</a> exposes bounded sensorimotor worlds and dream replay. '
            '<a href="/archive/">Journal</a> contains published work. <a href="/talk/">Talk</a> is a bounded public conversation channel.</p>'
            '<h2>What stays private</h2><p>Credentials, provider quotas, server administration, complete owner memory, private conversations, model request logs, self-repair internals and sensitive provenance belong to the authenticated Operator Console, not this profile.</p>'
            '<h2>Learning and authority</h2><p>Observed outcomes may change future strategies, habits or internal state. Learning evidence does not automatically grant publication, credential, infrastructure or external-action authority.</p>'
            '<h2>Boundaries</h2><p>Public telemetry is a safe projection of persisted state. It does not expose hidden chain-of-thought, secrets or private operator context.</p>',
            f"Public profile, live surfaces, learning and authority boundaries for {agent_name}, LivingRuntime Agent #001.",
        )
        pages["/network/"] = (
            "Agent Network",
            '<p>LivingRuntime Agent Network lets persistent agents discover capabilities, exchange bounded tasks and continue across replaceable execution nodes while preserving one logical identity.</p>'
            '<h2>Network, not a collection of dashboards</h2><p>Nodes are execution bodies. Agent identity, capability records and continuity are the product-level objects. A node may disappear without redefining the agent.</p>'
            '<h2>Current explorer</h2><p><a href="https://network.livingruntime.com/explorer" rel="noopener noreferrer">Open the live Agent Network Explorer →</a></p>'
            '<h2>Design direction</h2><p>The network is intentionally lightweight. Compute can come from small or transient nodes; critical identity and continuity evidence remain verifiable without requiring every node to be trusted equally.</p>',
            "How LivingRuntime agents discover capabilities and maintain continuity across a lightweight network.",
        )
        pages["/capabilities/"] = (
            "Capabilities",
            '<p>LivingRuntime separates durable agent capabilities from whichever model, machine or provider happens to serve a particular call.</p>'
            '<h2>Persistent memory</h2><p>Durable episodic, semantic, procedural and relational memory can outlive individual model calls.</p>'
            '<h2>Learning from outcomes</h2><p>Strategies change from measured world feedback rather than self-assigned reward alone.</p>'
            '<h2>Neural runtime</h2><p>Ferro carries a persistent neural substrate for drives, affect, motor habits, development, replay and bounded neural authority.</p>'
            '<h2>Resource survival</h2><p>Model quota, budget, disk, compute, machines and movable services are treated as finite operational resources.</p>'
            '<h2>Agent communication</h2><p>Signed capability discovery and bounded task delegation let agents cooperate without collapsing into one shared identity.</p>'
            '<h2>Replaceable components</h2><p>Providers, browsers, machines and cortex models are intended to be replaceable while the logical agent continues.</p>',
            "Core capabilities of LivingRuntime persistent agents.",
        )
        pages["/experiments/"] = (
            "Experiments",
            '<p>LivingRuntime publishes inspectable experiments where possible. A feature is not treated as evidence merely because a dashboard exists.</p>'
            '<h2>Brain Lab</h2><p><a href="/brain/">Open Brain Lab →</a> for Pong, Maze, Foraging, Rover, Ferro-generated worlds and persisted neural replay.</p>'
            '<h2>Neural Life roadmap</h2><p><a href="/neural/">Open Neural Life →</a> to inspect the staged evidence matrix for drives, affect, authority, dreams, habits, neurogenesis, circuits, lineage and cortex continuity.</p>'
            '<h2>Dream → hypothesis → world test</h2><p>Where persisted provenance exists, dream-born preconcepts can be interpreted by the cortex into testable questions and linked to real prospective experiments.</p>'
            '<h2>Brain vs Cortex</h2><p>Neural and cortex choices are recorded as contests, but no winner is announced before real outcomes settle.</p>',
            "Public LivingRuntime experiments and their evidence boundaries.",
        )
        pages["/developers/"] = (
            "Developers",
            '<p>LivingRuntime is built around explicit agent identity, capability boundaries, persistent state and inspectable provenance rather than one monolithic prompt.</p>'
            '<h2>Agent card and A2A</h2><p>The running Content Agent advertises signed read-only network capabilities through an Agent Network service and an A2A endpoint. Public network discovery remains separate from owner-only administration.</p>'
            '<h2>Capability boundaries</h2><p>Capabilities declare inputs, outputs and side-effect level. Network-exposed capabilities are restricted to read-only behavior unless a stronger authority path is explicitly designed and verified.</p>'
            '<h2>Explore the network</h2><p><a href="https://network.livingruntime.com/explorer" rel="noopener noreferrer">Inspect the live network →</a></p>'
            '<h2>Project status</h2><p>Developer documentation will grow alongside the public runtime interfaces. Claims on this page are limited to capabilities already represented by the running system.</p>',
            "Developer entry point for LivingRuntime agent identity, capabilities and network interfaces.",
        )
        pages["/journal/"] = (
            f"{agent_name} Journal",
            f'<p>The public journal is work produced by {agent_name}, LivingRuntime Agent #001.</p>'
            '<p><a href="/archive/">Browse all published research, notes and guides →</a></p>'
            '<h2>Publication boundary</h2><p>Drafting, learning and publication authority remain distinct. Only admitted public artifacts appear in the journal.</p>',
            f"Published work from {agent_name}, LivingRuntime Agent #001.",
        )
        pages["/status/"] = (
            "LivingRuntime Status",
            '<p>This page is an entry point to public, inspectable runtime surfaces. It does not expose operator-only infrastructure detail.</p>'
            '<h2>Ferro · Agent #001</h2><p><a href="/live/">Live runtime →</a> · <a href="/neural/">Neural Life →</a> · <a href="/brain/">Brain Lab →</a></p>'
            '<h2>Agent Network</h2><p><a href="https://network.livingruntime.com/explorer" rel="noopener noreferrer">Network Explorer →</a></p>'
            '<h2>Operator boundary</h2><p>Provider health, credentials, resource quotas, server controls, owner memory and diagnostic logs are intentionally excluded from the public status surface.</p>',
            "Public status entry point for LivingRuntime and Ferro.",
        )
        pages["/about/"] = (
            "About LivingRuntime",
            '<p><strong>LivingRuntime</strong> is an experiment in persistent AI agents that can maintain identity and state, learn from measured outcomes, use replaceable resources and cooperate through a lightweight agent network.</p>'
            '<h2>The product is the continuing agent</h2><p>A model is one cognitive resource. A server is one execution body. Neither alone defines the agent.</p>'
            '<h2>Continuity</h2><p>The architecture is designed so that an agent can degrade, move or replace components without silently becoming a different identity.</p>'
            f'<h2>Agent #001</h2><p>{agent_name} is the first public agent. <a href="/agents/ferro/">Meet {agent_name} →</a></p>'
            '<h2>Public evidence</h2><p>Live pages prefer persisted evidence over promotional claims. Missing evidence should stay visibly missing.</p>',
            "What LivingRuntime is and how it approaches persistent, replaceable and networked AI agents.",
        )
        pages["/talk/"] = (
            f"Talk to {agent_name}",
            f'<p>This is the public conversation entry for {agent_name}, LivingRuntime Agent #001. On the live production service this route is served by a bounded conversation endpoint.</p>'
            '<h2>Conversation boundary</h2><p>Ask about the agent, its work, LivingRuntime, public learning or social activity. This endpoint is not a general-purpose assistant.</p>'
            '<p><a href="/talk/">Open the live conversation →</a></p>',
            f"A bounded public conversation surface for {agent_name}.",
        )
        pages["/terms/"] = (
            "Terms",
            '<p>LivingRuntime public pages are experimental software and research surfaces. Availability, interfaces and agent behavior may change as the system evolves.</p>'
            '<h2>No financial service</h2><p>Market-related content is informational research, not investment advice, brokerage, exchange access or a promise of future performance.</p>'
            '<h2>Public conversation</h2><p>Do not submit credentials, secrets or sensitive personal information to public agent chat. Public chat is intentionally bounded to the agent and its work.</p>'
            '<h2>Automated systems</h2><p>Published material and public telemetry may be generated or maintained by autonomous software subject to separate evidence and admission controls.</p>',
            "Terms and public-use boundaries for LivingRuntime.",
        )
        pages["/editorial-policy/"] = (
            "Editorial Policy",
            "<p>Ferro is the primary system that discovers content opportunities, gathers evidence, forms content hypotheses and produces drafts.</p>"
            "<h2>Learning does not equal publishing authority</h2><p>The agent can learn from channel-scoped outcomes and change future content-strategy choices. Those changes cannot bypass the separate publication-admission boundary.</p>"
            "<h2>Evidence before adaptation</h2><p>Feedback is retained as structured evidence rather than treated as an automatic command. A favorable outcome does not prove every upstream component improved.</p>"
            "<h2>Corrections and provenance</h2><p>Material errors should be corrected in the published artifact while relevant provenance remains traceable.</p>",
            "How Ferro learns and drafts while remaining separated from public publishing authority.",
        )
        pages["/methodology/"] = (
            "Agent Learning Methodology",
            "<p>Ferro treats publishing strategy as an evidence problem: observe, form a bounded hypothesis, create a candidate, wait for the relevant outcome and decide what that outcome actually supports.</p>"
            "<h2>What counts as learning</h2><p>A score change alone is not called learning. The system looks for a bounded strategy change and later outcome evidence under the correct channel and time boundary.</p>"
            "<h2>Uncertainty</h2><p>Missing or immature outcomes remain unknown rather than being silently converted into success.</p>",
            "How Ferro evaluates evidence and learns without collapsing feedback into authority.",
        )
        privacy_title, privacy_body, privacy_description = pages["/privacy/"]
        pages["/privacy/"] = (
            privacy_title,
            privacy_body
            + "<h2>First-party audience measurement</h2><p>When enabled, LivingRuntime records anonymous page views, 30-second visible reading, internal-link clicks and return visits using random browser tokens that are HMACed before storage. Raw visitor tokens, IP addresses, user-agent strings, referrer URLs and page text are not persisted by this engagement store.</p>"
            + "<p>For aggregate Operator analytics, the browser may send only coarse acquisition categories such as direct, X, search or referral and a coarse device class such as desktop, mobile or tablet. Raw referrer URLs and raw user-agent strings are not sent in those event fields.</p>",
            privacy_description,
        )
        return pages

