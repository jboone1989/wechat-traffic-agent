"""Explicit Website-only automatic admission, audited builds and atomic activation."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .channels.agent_web import ContentAgentWebChannel
from .channels.web import StaticWebsiteConfig, _route_for
from .content_artifact import ContentArtifact
from .site_audit import audit_static_site
from .site_cli import _load_artifacts, _curate_web_taxonomy
from .web_editorial import digest
from .content_admission import _write_once
from .website_experiment import WebsiteExperimentBinding, experiment_compliance


class AutomaticWebsitePublisher:
    policy_version = 'website-automatic-admission:v1'

    def __init__(self, *, state_root, public_root, starter_path, config,
                 clock: Callable[[], datetime] | None = None):
        self.state = Path(state_root) / 'content/automatic_website'
        self.public = Path(public_root)
        self.starter = Path(starter_path)
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        private, public = Path(state_root).resolve(), self.public.resolve()
        if private == public or private in public.parents or public in private.parents:
            raise ValueError('private state and public site must be separate')

    def inventory_path(self):
        current = self.public / 'current'
        if not current.is_symlink():
            raise ValueError('operator must initialize current site symlink')
        revision = current.resolve().name
        path = self.state / 'inventories' / (revision + '.json')
        return path if path.exists() else self.starter

    def publish(self, *, artifact: ContentArtifact, experience: dict, cycle_root: Path):
        if artifact.public_safe or not experience.get('automated_checks_passed'):
            raise ValueError('automatic admission requires a private draft with passing checks')
        if experience['draft_sha256'] != digest(artifact.model_dump(mode='json')):
            raise ValueError('self-check is not bound to this draft')
        experiment = _validated_experiment(artifact, experience)
        # This is a distinct machine policy, never a fabricated human review.
        public_artifact = artifact.model_copy(update={'public_safe': True})
        artifacts = _load_artifacts(self.inventory_path())
        existing = next((a for a in artifacts if a.artifact_id == artifact.artifact_id), None)
        if existing:
            if existing != public_artifact:
                raise ValueError('published artifact ID conflict')
        else:
            artifacts.append(public_artifact)
        payload = [a.model_dump(mode='json') for a in artifacts]
        revision = digest({'artifacts': payload, 'config': self.config.model_dump(mode='json'), 'policy': self.policy_version})
        target = self.public / 'releases' / revision
        if not target.exists():
            AutomaticAgentWebChannel(self.config).build(_curate_web_taxonomy(artifacts), target)
        audit = audit_static_site(target, expect_indexable=self.config.indexable)
        if not audit.passed:
            raise ValueError('automatic website candidate audit failed')
        # tempfile build roots start at 0700; audited public output must be readable by Nginx.
        for entry in target.rglob('*'):
            entry.chmod(0o755 if entry.is_dir() else 0o644)
        target.chmod(0o755)
        target.parent.chmod(0o755)
        inventory = self.state / 'inventories' / (revision + '.json')
        _write_once(inventory, json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
        topic = artifact.tags[0] if artifact.tags else None
        lesson_id = experience.get('lesson_id')
        if not topic or not isinstance(lesson_id, str) or not lesson_id.strip():
            raise ValueError('automatic outcome attribution requires topic and lesson_id')
        candidate_content = _public_content_digest(artifact)
        published_content = _public_content_digest(public_artifact)
        if candidate_content != published_content:
            raise ValueError('automatic admission changed reader-visible content')
        pending = self.public / 'current.next'
        pending.unlink(missing_ok=True)
        pending.symlink_to(target)
        published_at = self.clock()
        if not isinstance(published_at, datetime) or published_at.utcoffset() is None:
            pending.unlink(missing_ok=True)
            raise ValueError('publication clock must return a timezone-aware datetime')
        published_at = published_at.astimezone(timezone.utc)
        os.replace(pending, self.public / 'current')
        attribution = {
            'schema_version': ('website-publication-attribution.v2' if experiment is not None
                               else 'website-publication-attribution.v1'),
            'published_at': published_at.isoformat(),
            'public_route': _route_for(public_artifact),
            'published_content_sha256': published_content,
            'agent_candidate_sha256': candidate_content,
            'topic': topic,
            'title_version': 'sha256:' + digest({'title': artifact.title}),
            'structure_version': 'sha256:' + digest({
                'kind': artifact.kind.value,
                'headings': [section.heading for section in artifact.sections],
            }),
            'strategy_version': (experiment.strategy_version if experiment is not None
                                 else 'lesson:' + lesson_id.strip()),
            'practice_version': 'lesson:' + lesson_id.strip(),
        }
        if experiment is not None:
            attribution['experiment'] = experiment.model_dump(mode='json')
        receipt = {'policy_version': self.policy_version, 'reviewer_kind': 'AUTOMATED',
                   'draft_sha256': experience['draft_sha256'], 'experience_sha256': digest(experience),
                   'revision': revision, 'artifact_id': artifact.artifact_id,
                   'audit': audit.model_dump(mode='json'), 'public_root': str(target),
                   'outcome_attribution': attribution}
        _write_once(cycle_root / 'publication.json', json.dumps(receipt, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
        return receipt


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
    if recomputed['status'] != 'PASS':
        raise ValueError('automatic publication requires actual Website experiment compliance')
    if not isinstance(compliance, dict) or compliance != recomputed:
        raise ValueError('persisted Website experiment compliance does not match recomputation')
    return binding


def _public_content_digest(artifact: ContentArtifact) -> str:
    payload = artifact.model_dump(mode='json')
    payload.pop('public_safe', None)
    return digest(payload)


class AutomaticAgentWebChannel(ContentAgentWebChannel):
    """Retain the agent-first presentation while describing this active policy."""

    def _home(self, artifacts, categories):
        page = super()._home(artifacts, categories)
        page = page.replace('public admission remains a separate human-controlled decision',
                            'public admission is a separate automated source and Website quality gate')
        page = page.replace('Human-gated publishing', 'Audited automatic publishing')
        page = page.replace('Use outcomes to improve the next choice', 'Practice, review, and adapt the next article')
        page = page.replace('Channel-scoped outcomes become durable evidence for future strategy decisions instead of silently rewriting past results.',
                            'Versioned writing exercises and article self-checks inform the next run. Mature Website pageview outcomes are recorded separately as descriptive evidence and do not yet control strategy.')
        page = page.replace('Outcome evidence can change future content strategy, but it cannot bypass editorial admission.',
                            'Writing experience informs the next attempt. Automatic publication requires a separate source check and complete Website audit.')
        return page

    def _static_pages(self):
        pages = super()._static_pages()
        pages['/about/'] = (
            'About the Content Agent',
            '<p>This website is the public interface of a persistent Content Agent. It reads current public coverage, '
            'uses existing Topic coverage and links to choose questions, practices writing techniques, and creates original explanations.</p>'
            '<h2>What self-learning means here</h2><p>The current loop retains writing exercises, article self-checks and '
            'specific next-practice tasks across runs. Later writing receives those experiences as input. '
            'This is recorded practice and adaptation, not proof of improved audience outcomes.</p>'
            '<h2>Automatic Website publication</h2><p>The operator has enabled automatic Website publication after '
            'source-grounding, technique-application and Website integrity checks. These automated checks can be wrong. '
            'Original drafts and their review history remain separate from admitted public copies.</p>'
            '<h2>Persistent identity</h2><p>Runtime, VirtualBrain and Agent Network preserve the agent identity and '
            'research continuity. This connection does not grant Trading execution permissions.</p>',
            'The persistent agent, its writing practice and automatic Website output.'
        )
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
        return pages
