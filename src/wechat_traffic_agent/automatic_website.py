"""Explicit Website-only automatic admission, audited builds and atomic activation."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .channels.web import StaticWebChannel, StaticWebsiteConfig
from .content_artifact import ContentArtifact
from .site_audit import audit_static_site
from .site_cli import _load_artifacts, _curate_web_taxonomy
from .web_editorial import digest
from .content_admission import _write_once


class AutomaticWebsitePublisher:
    policy_version = 'website-automatic-admission:v1'

    def __init__(self, *, state_root, public_root, starter_path, config):
        self.state = Path(state_root) / 'content/automatic_website'
        self.public = Path(public_root)
        self.starter = Path(starter_path)
        self.config = config
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
            StaticWebChannel(self.config).build(_curate_web_taxonomy(artifacts), target)
        audit = audit_static_site(target, expect_indexable=self.config.indexable)
        if not audit.passed:
            raise ValueError('automatic website candidate audit failed')
        inventory = self.state / 'inventories' / (revision + '.json')
        _write_once(inventory, json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
        receipt = {'policy_version': self.policy_version, 'reviewer_kind': 'AUTOMATED',
                   'draft_sha256': experience['draft_sha256'], 'experience_sha256': digest(experience),
                   'revision': revision, 'artifact_id': artifact.artifact_id,
                   'audit': audit.model_dump(mode='json'), 'public_root': str(target)}
        _write_once(cycle_root / 'publication.json', json.dumps(receipt, sort_keys=True, ensure_ascii=False, indent=2) + '\n')
        pending = self.public / 'current.next'
        pending.unlink(missing_ok=True)
        pending.symlink_to(target)
        os.replace(pending, self.public / 'current')
        return receipt
