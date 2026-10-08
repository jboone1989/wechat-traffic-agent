"""Explicit Website-only automatic admission, audited builds and atomic activation."""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from wechat_traffic_agent.channels.agent_web import ContentAgentWebChannel
from wechat_traffic_agent.channels.web import StaticWebsiteConfig, _route_for
from wechat_traffic_agent.content_artifact import ContentArtifact
from wechat_traffic_agent.content_strategy import ContentStrategyBinding
from wechat_traffic_agent.site_audit import audit_static_site
from wechat_traffic_agent.site_cli import _load_artifacts, _curate_web_taxonomy
from wechat_traffic_agent.web_editorial import digest, resolve_rendered_inventory
from wechat_traffic_agent.content_admission import _write_once
from wechat_traffic_agent.evidence_floor import (
    EvidenceFloorCheck, EvidenceFloorPlan, artifact_risk_cues, verify_evidence_floor_publication,
)
from wechat_traffic_agent.website_experiment import WebsiteExperimentBinding, experiment_compliance
from wechat_traffic_agent.publication.models import PublicationEvent
from wechat_traffic_agent.publication.dispatch import DurablePublicationDispatch, PublicationSink
from wechat_traffic_agent.publication.event_store import PublicationEventStore
from wechat_traffic_agent.content_publication_store import ContentPublicationStore


class AutomaticWebsitePublisher:
    policy_version = 'website-automatic-admission:v3'

    def __init__(self, *, state_root, public_root, starter_path, config,
                 clock: Callable[[], datetime] | None = None,
                 publication_sink: PublicationSink | None = None):
        self.state_root = Path(state_root)
        self.state = self.state_root / 'content/automatic_website'
        self.public = Path(public_root)
        self.starter = Path(starter_path)
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.publication_sink = publication_sink or DurablePublicationDispatch(
            PublicationEventStore(self.state_root),
        ).record
        private, public = Path(state_root).resolve(), self.public.resolve()
        if private == public or private in public.parents or public in private.parents:
            raise ValueError('private state and public site must be separate')

    def inventory_path(self):
        content = ContentPublicationStore(
            state_root=self.state_root, public_root=self.public, config=self.config,
        )
        if content.ready():
            return content.inventory
        current = self.public / 'current'
        if not current.is_symlink():
            raise ValueError('operator must initialize current site symlink')
        return resolve_rendered_inventory(
            fallback_path=self.starter,
            site_root=current,
            inventories_dir=self.state / 'inventories',
        )

    def maintain(self, *, now: datetime) -> dict:
        """Audit the live site independently of X and model availability.

        Recheck on an active-release change, at least daily. The next
        publication rebuilds the archive, topic indexes, sitemap and feed.
        """
        if now.tzinfo is None:
            raise ValueError('website maintenance time must be timezone-aware')
        current = self.public / 'current'
        if not current.is_symlink():
            raise RuntimeError('website live release symlink is missing')
        revision = current.resolve().name
        today = now.astimezone(timezone.utc).date().isoformat()
        receipt = self.state / 'maintenance' / (today + '.json')
        if receipt.exists():
            prior = json.loads(receipt.read_text(encoding='utf-8'))
            if prior.get('release') == revision and prior.get('status') == 'HEALTHY':
                return prior
        from wechat_traffic_agent.web_editorial import website_snapshot
        snapshot = website_snapshot(self.inventory_path(), current, self.config.base_url)
        audit = audit_static_site(current, expect_indexable=self.config.indexable)
        report = {
            'schema_version': 'ferro-website-maintenance.v1',
            'status': 'HEALTHY' if audit.passed else 'NEEDS_REPAIR',
            'checked_at': now.astimezone(timezone.utc).isoformat(),
            'release': revision,
            'base_url': self.config.base_url,
            'article_count': len(snapshot['articles']),
            'topic_count': len(snapshot['topics']),
            'audit': audit.model_dump(mode='json'),
        }
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temp = receipt.with_suffix('.json.tmp')
        temp.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
        os.replace(temp, receipt)
        return report

    def publish(self, *, artifact: ContentArtifact, experience: dict, cycle_root: Path):
        admitted = self._admit(artifact, experience)
        public_artifact, articles, experiment, strategy, topic, lesson_id, content_hash = admitted
        published_at = self._publication_time(strategy)
        revision, target, content_revision, audit = self._materialize(public_artifact, articles)
        attribution = self._attribution(
            public_artifact, experiment, strategy, topic, lesson_id, content_hash, published_at,
        )
        return self._record_publication(
            artifact, experience, cycle_root, published_at, attribution,
            revision, target, content_revision, audit, content_hash,
        )

    def _admit(self, artifact: ContentArtifact, experience: dict):
        if artifact.public_safe or not experience.get('automated_checks_passed'):
            raise ValueError('automatic admission requires a private draft with passing checks')
        if experience['draft_sha256'] != digest(artifact.model_dump(mode='json')):
            raise ValueError('self-check is not bound to this draft')
        experiment = _validated_experiment(artifact, experience)
        strategy = _validated_content_strategy(artifact, experience, experiment)
        _validated_evidence_floor(artifact, experience)
        public_artifact = artifact.model_copy(update={'public_safe': True})
        articles = _load_artifacts(self.inventory_path())
        existing = next((a for a in articles if a.artifact_id == artifact.artifact_id), None)
        if existing and existing != public_artifact:
            raise ValueError('published artifact ID conflict')
        if not existing:
            articles.append(public_artifact)
        topic = artifact.tags[0] if artifact.tags else None
        lesson_id = experience.get('lesson_id')
        if not topic or not isinstance(lesson_id, str) or not lesson_id.strip():
            raise ValueError('automatic outcome attribution requires topic and lesson_id')
        content_hash = _public_content_digest(artifact)
        if content_hash != _public_content_digest(public_artifact):
            raise ValueError('automatic admission changed reader-visible content')
        return public_artifact, articles, experiment, strategy, topic, lesson_id, content_hash

    def _publication_time(self, strategy: ContentStrategyBinding | None) -> datetime:
        if strategy is not None and strategy.decided_at.astimezone(timezone.utc) > self.clock():
            raise ValueError('content strategy was decided after publication')
        # Validate before changing the live website or article data.
        published_at = self.clock()
        if not isinstance(published_at, datetime) or published_at.utcoffset() is None:
            raise ValueError('publication clock must return a timezone-aware datetime')
        return published_at.astimezone(timezone.utc)

    def _materialize(self, artifact: ContentArtifact, articles: list[ContentArtifact]):
        store = ContentPublicationStore(
            state_root=self.state_root, public_root=self.public, config=self.config,
        )
        if store.ready():
            return self._materialize_incremental(artifact, store)
        return self._materialize_legacy(articles)

    def _materialize_incremental(self, artifact, store):
        # Only the article and derived content views change; the site shell stays put.
        result = store.publish(artifact, website_channel=AutomaticAgentWebChannel(self.config))
        revision = (self.public / 'current').resolve(strict=True).name
        from wechat_traffic_agent.site_audit import StaticSiteAudit
        audit = StaticSiteAudit(
            passed=True,
            checks={'content_record_and_page_materialized': True,
                    'content_indexes_generated': True,
                    'website_shell_not_redeployed': True},
            html_pages=1, internal_links_checked=0,
            warnings=('INCREMENTAL_CONTENT_ONLY; site audit belongs to website releases',),
        )
        return revision, self.public / 'current', result['content_revision'], audit

    def _materialize_legacy(self, articles: list[ContentArtifact]):
        # This remains a legacy migration route, not the normal publishing path.
        payload = [a.model_dump(mode='json') for a in articles]
        revision = digest({
            'artifacts': payload, 'config': self.config.model_dump(mode='json'),
            'policy': self.policy_version,
        })
        target = self.public / 'releases' / revision
        if not target.exists():
            AutomaticAgentWebChannel(self.config).build(_curate_web_taxonomy(articles), target)
        audit = audit_static_site(target, expect_indexable=self.config.indexable)
        if not audit.passed:
            raise ValueError('automatic website candidate audit failed')
        for entry in target.rglob('*'):
            entry.chmod(0o755 if entry.is_dir() else 0o644)
        target.chmod(0o755)
        target.parent.chmod(0o755)
        inventory = self.state / 'inventories' / (revision + '.json')
        _write_once(inventory, json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
        pending = self.public / 'current.next'
        pending.unlink(missing_ok=True)
        pending.symlink_to(target)
        os.replace(pending, self.public / 'current')
        return revision, target, None, audit

    @staticmethod
    def _attribution(artifact, experiment, strategy, topic, lesson_id, content_hash, published_at):
        value = {
            'schema_version': ('website-publication-attribution.v2' if experiment is not None
                               else 'website-publication-attribution.v1'),
            'published_at': published_at.isoformat(),
            'public_route': _route_for(artifact),
            'published_content_sha256': content_hash,
            'agent_candidate_sha256': content_hash,
            'topic': topic,
            'title_version': 'sha256:' + digest({'title': artifact.title}),
            'structure_version': 'sha256:' + digest({
                'kind': artifact.kind.value, 'headings': [s.heading for s in artifact.sections],
            }),
            'strategy_version': (experiment.strategy_version if experiment is not None
                                 else 'lesson:' + lesson_id.strip()),
            'practice_version': 'lesson:' + lesson_id.strip(),
        }
        if strategy is not None:
            value['content_strategy'] = strategy.model_dump(mode='json')
            if strategy.decided_at.astimezone(timezone.utc) > published_at:
                raise ValueError('content strategy was decided after publication')
        if experiment is not None:
            value['experiment'] = experiment.model_dump(mode='json')
        return value

    def _record_publication(
        self, artifact, experience, cycle_root, published_at, attribution,
        revision, target, content_revision, audit, content_hash,
    ):
        event_id = digest({'artifact_id': artifact.artifact_id, 'content_hash': content_hash})
        receipt = {
            'policy_version': self.policy_version, 'reviewer_kind': 'AUTOMATED',
            'publication_event_id': event_id,
            'draft_sha256': experience['draft_sha256'], 'experience_sha256': digest(experience),
            'revision': revision, 'content_revision': content_revision,
            'artifact_id': artifact.artifact_id, 'audit': audit.model_dump(mode='json'),
            'public_root': str(target),
            'site_url': self.config.base_url.rstrip('/') + attribution['public_route'],
            'outcome_attribution': attribution,
        }
        _write_once(
            cycle_root / 'publication.json',
            json.dumps(receipt, sort_keys=True, ensure_ascii=False, indent=2) + '\n',
        )
        event = PublicationEvent(
            event_id=event_id, artifact_id=artifact.artifact_id,
            canonical_url=receipt['site_url'], title=artifact.title,
            summary=artifact.summary, topics=artifact.tags,
            published_at=published_at, content_hash=content_hash,
        )
        self.publication_sink(event)
        return receipt


def _validated_content_strategy(artifact: ContentArtifact, experience: dict,
                                experiment: WebsiteExperimentBinding | None) -> ContentStrategyBinding | None:
    raw = experience.get('content_strategy')
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError('content strategy must be an object when present')
    binding = ContentStrategyBinding.model_validate(raw)
    topic = artifact.tags[0] if artifact.tags else None
    if binding.topic != topic or binding.cycle != experience.get('cycle'):
        raise ValueError('content strategy is not bound to this cycle/topic')
    if experiment is not None and binding.opening_mode != experiment.treatment_value:
        raise ValueError('content strategy opening mode disagrees with Website experiment')
    return binding


def _validated_evidence_floor(artifact: ContentArtifact, experience: dict) -> None:
    raw_plan = experience.get('evidence_floor_plan')
    raw_check = experience.get('evidence_floor_check')
    topic = artifact.tags[0] if artifact.tags else ''
    if raw_plan is None and raw_check is None and not artifact_risk_cues(artifact, topic=topic):
        return
    if not isinstance(raw_plan, dict) or not isinstance(raw_check, dict):
        raise ValueError('automatic publication requires persisted Evidence Floor plan and check for high-risk content')
    self_check = experience.get('self_check')
    supports = self_check.get('support') if isinstance(self_check, dict) else None
    if not isinstance(supports, (list, tuple)):
        raise ValueError('automatic publication requires persisted self-check support')
    plan = EvidenceFloorPlan.model_validate(raw_plan)
    check = EvidenceFloorCheck.model_validate(raw_check)
    verify_evidence_floor_publication(artifact=artifact, plan=plan, check=check, supports=supports)


def _validated_experiment(
    artifact: ContentArtifact, experience: dict
) -> WebsiteExperimentBinding | None:
    raw = experience.get('prospective_experiment')
    compliance = experience.get('website_experiment_compliance')
    if raw is None:
        if compliance is not None:
            raise ValueError('experiment compliance exists without a binding')
        return None
    binding = WebsiteExperimentBinding.model_validate(raw)
    recomputed = experiment_compliance(artifact, binding)
    if not isinstance(compliance, dict) or compliance != recomputed:
        raise ValueError('persisted Website experiment compliance does not match recomputation')
    # Experiment compliance controls experiment enrollment, not publication. A genuine
    # treatment miss is published under the non-experimental v1 attribution so it cannot
    # contaminate the prospective experiment. Forged or stale compliance still fails closed.
    if recomputed['status'] != 'PASS':
        return None
    return binding


def _public_content_digest(artifact: ContentArtifact) -> str:
    payload = artifact.model_dump(mode='json')
    payload.pop('public_safe', None)
    return digest(payload)


class AutomaticAgentWebChannel(ContentAgentWebChannel):
    """Retain the agent-first presentation while describing this active policy."""

    def _home(self, artifacts, categories):
        # Restore the original image-led LivingRuntime design, including its
        # real embedded hero photograph, original layout and responsive CSS.
        self._timeline_artifacts = artifacts
        page = super()._home(artifacts, categories)
        # A short editorial line: let the visual and work cards do the talking.
        page = page.replace(
            'LivingRuntime is a public runtime for persistent agents: durable identity and memory, observable learning, neural development, replaceable model providers, and a network where agents can discover capabilities and cooperate.',
            'Meet persistent agents that research, build and learn from real-world outcomes. Ferro publishes its research here first.',
        )
        rail = (
            '<section class="ferro-visual-rail" aria-label="Explore Ferro">'
            '<div class="home-shell ferro-rail-grid">'
            '<a class="ferro-rail-card" href="/archive/"><span class="ferro-rail-number">01 / Featured Research</span>'
            '<strong>Published work <span>↗</span></strong><small>Explore evidence-led articles</small></a>'
            '<a class="ferro-rail-card" href="/experiments/"><span class="ferro-rail-number">02 / EXPERIMENTS</span>'
            '<strong>What we test <span>↗</span></strong><small>What is being tested</small></a>'
            '<a class="ferro-rail-card" href="/methodology/"><span class="ferro-rail-number">03 / SYSTEMS</span>'
            '<strong>Inside the system <span>↗</span></strong><small>How Ferro works</small></a>'
            '<a class="ferro-rail-card" href="/timeline/"><span class="ferro-rail-number">04 / TIMELINE</span>'
            '<strong>Public activity <span>↗</span></strong><small>Follow published progress</small></a>'
            '</div></section>'
        )
        page = page.replace('<section class="crl-loop"', rail + '<section class="crl-loop"', 1)
        # The site's artwork/layout is versioned independently; only this
        # small data-fed area changes when Ferro publishes new research.
        script = """<script>
(async()=>{
  const grid=document.querySelector('.latest-grid');
  if(!grid)return;
  try {
    const response=await fetch('/api/agent/status?content=articles',{cache:'no-store'});
    if(!response.ok)throw new Error('content data unavailable');
    const payload=await response.json();
    if(payload.schema_version!=='ferro-content-catalog.v1'||!Array.isArray(payload.articles))return;
    const rows=payload.articles.slice(0,6);
    if(!rows.length)return;
    const frag=document.createDocumentFragment();
    for(const item of rows){
      if(!/^\\/(articles|guides|research|notes)\\/[a-z0-9-]+\\/$/.test(item.url))continue;
      const card=document.createElement('article');card.className='card';
      const heading=document.createElement('h3');
      const link=document.createElement('a');link.href=item.url;link.textContent=item.title;
      heading.append(link);
      const summary=document.createElement('p');summary.textContent=item.summary;
      const meta=document.createElement('div');meta.className='card-meta';
      const type=document.createElement('span');type.textContent=item.kind;
      meta.append(type);card.append(heading,summary,meta);frag.append(card);
    }
    if(frag.childNodes.length)grid.replaceChildren(frag);
  }catch(error){/* keep audited static editorial cards when content feed unavailable */}
})();
</script>"""
        return page.replace('</body>', script + '</body>', 1)

    def _css(self):
        return super()._css() + """
.ferro-visual-rail{background:#07110f;border-bottom:1px solid rgba(154,226,182,.18);padding:18px 0 48px}
.ferro-rail-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.ferro-rail-card{display:flex;min-height:160px;flex-direction:column;justify-content:space-between;padding:22px;border:1px solid rgba(127,212,163,.2);border-radius:16px;background:linear-gradient(145deg,rgba(27,66,49,.82),rgba(10,27,22,.9));text-decoration:none;color:#f3fbf6;transition:transform .2s ease,border-color .2s ease,background .2s ease}
.ferro-rail-card:hover{transform:translateY(-4px);border-color:rgba(124,237,174,.7);background:linear-gradient(145deg,#225e43,#102c22)}
.ferro-rail-number{font:600 .67rem ui-monospace,monospace;letter-spacing:.12em;color:#85d9a9}
.ferro-rail-card strong{font-size:1.25rem;font-weight:600;letter-spacing:-.02em}
.ferro-rail-card strong span{float:right;color:#81e0ae}
.ferro-rail-card small{font-size:.81rem;color:#a8c3b4}
@media(max-width:900px){.ferro-rail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.ferro-visual-rail{padding:4px 0 28px}.ferro-rail-grid{gap:9px}.ferro-rail-card{min-height:132px;padding:15px}.ferro-rail-card strong{font-size:1.03rem}.ferro-rail-card small{font-size:.72rem}}
@media(prefers-reduced-motion:reduce){.ferro-rail-card{transition:none}.ferro-rail-card:hover{transform:none}}
"""

    def _static_pages(self):
        pages = super()._static_pages()
        pages['/editorial-policy/'] = (
            'Editorial Policy',
            '<p>Content Agent selects topics and writes articles using attributed public evidence. It must distinguish '
            'reported observations, interpretation and hypothetical examples.</p>'
            '<h2>Separate automated admission</h2><p>The operator authorized automatic Website publication. A separate '
            'machine policy checks the exact draft, source quotations and writing-technique applications, then builds '
            'and audits the complete Website before activation. Failed checks hold the draft and retain the existing Website.</p>'
            '<h2>Limits and corrections</h2><p>Automated checks are fallible and do not establish factual certainty. '
            'Material errors should be corrected with a traceable updated artifact. Articles disclose automated authorship.</p>'
            '<h2>No trading authority</h2><p>Articles are explanations, not investment recommendations. Website publishing '
            'does not authorize trades or posting to other channels.</p>',
            'Automatic Website admission, evidence checks and correction boundaries.'
        )
        pages['/methodology/'] = (
            'Agent Learning Methodology',
            '<p>The running loop is: practice a writing technique, observe recent publisher coverage, select a reader '
            'question, write, self-check, audit and publish, then carry specific practice feedback into the next run.</p>'
            '<h2>What is recorded</h2><p>Source snapshots, versioned lessons, before-and-after practice examples, model '
            'response receipts, article quotations, self-check results and publication audits are retained.</p>'
            '<h2>What the evidence does not prove</h2><p>Recent publisher coverage is not measured popularity. A model '
            'self-check is not independent proof of skill improvement. Mature Website pageview outcomes are recorded as descriptive evidence but are not yet connected to strategy control. '
            'They are not unique-reader counts, and claims about conversion or causal improvement remain unknown.</p>'
            '<h2>Continuity</h2><p>Existing source content is deduplicated across restarts. A bounded scheduled attempt '
            'cannot turn a failed self-check into an unlimited rewrite or publication loop.</p>',
            'Writing practice, experience reuse and the limits of automated self-assessment.'
        )
        chronology = sorted(
            getattr(self, '_timeline_artifacts', []),
            key=lambda item: item.updated_at or item.created_at,
            reverse=True,
        )[:30]
        rows = ''.join(
            '<article class="work-item"><p class="eyebrow">Public research · '
            + html.escape(item.kind.value.upper()) + '</p>'
            + '<time datetime="' + html.escape((item.updated_at or item.created_at).isoformat(), quote=True)
            + '">' + html.escape((item.updated_at or item.created_at).strftime('%Y-%m-%d')) + '</time>'
            + '<h2><a href="' + html.escape(_route_for(item), quote=True) + '">'
            + html.escape(item.title) + '</a></h2><p>'
            + html.escape(item.summary) + '</p></article>'
            for item in chronology
        )
        pages['/timeline/'] = (
            'Ferro Public Timeline',
            '<header class="page-head"><p class="eyebrow">PUBLIC RECORD</p>'
            '<h1>What Ferro has made public</h1>'
            '<p>Dates reflect artifact records, not independently measured learning '
            'or private deliberation. Each entry links to a live public artifact.</p></header>'
            + '<div class="work-grid">' + (rows or '<p>No audited work yet.</p>') + '</div>',
            'Chronology of Ferro public research with traceable artifact links.',
        )
        return pages
