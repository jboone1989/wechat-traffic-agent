# Content Agent — canonical product and migration plan

Status: ACTIVE  
Date: 2026-09-14  
Branch: `feat/content-agent-web`

## Goal

Evolve the current `wechat-traffic-agent` into a channel-independent Content Agent. The agent discovers worthwhile topics, consumes governed evidence from domain agents such as Trading through Agent Network, creates original content, learns from outcomes, and publishes through independent channel adapters.

The public website is the first production channel. WeChat remains a supported channel; it is no longer the product boundary.

## Target architecture

```text
Internet sources ───────────────────────────┐
                                            ├─> Content Agent ─> Website ─> SEO / AdSense
Trading ─> Agent Network research artifact ┘        │
                                                     ├─> WeChat
                                                     └─> future channels
```

Trading owns trading research, experiments, execution, and private operations. It does not own public presentation. Content Agent must never require Trading database access, exchange credentials, trading controls, current positions, or private operational APIs.

Cross-agent research transfer uses Agent Network discovery, signed task contracts, artifact manifests/references, and Network evidence. Content Agent must not add a bespoke Trading REST client or import Trading implementation modules.

## Phase 1 — foundation

- Freeze the product boundary in ADR-0010.
- Add a platform-neutral `ContentArtifact` with provenance and an explicit `public_safe` gate.
- Add a static Website channel that refuses unsafe artifacts, escapes untrusted text, and produces index/article/research pages plus SEO and AdSense integration points.
- Preserve existing WeChat APIs and behavior; no big-bang package rename.
- Add focused tests for public-safety gating, HTML escaping, deterministic routes, SEO files, and generated-site integrity.

Acceptance: existing WeChat behavior remains compatible; website tests are green; no Trading implementation dependency exists in the Content Agent package.

## Phase 2 — web-native content

- Add web-article generation using the existing topic discovery, Viral Radar, evidence, planner, and learning components.
- Keep `TietuDraft` as a WeChat-specific representation rather than the canonical content model.
- Add topic/archive pages, canonical URLs, structured data, RSS, policy pages, 404 handling, and production templates.
- Add channel-neutral performance observations so Website and WeChat can learn without mixing incompatible metrics.
- Ship a substantive starter corpus so the production site is not a thin shell while live content sources accumulate.

## Phase 3 — Trading research through Agent Network

### Provider side — Trading

- Add one dedicated read-only Network capability for publishable Trading research; do not expose the full Trading capability surface to Content Agent.
- The capability produces a versioned JSON artifact containing only delayed/sanitized research facts suitable for external editorial use, plus methodology/version, limitations, timestamps, and stable evidence references.
- The artifact must exclude credentials, account identifiers, current positions, order controls, live quantities, leverage, margin, private runtime state, and any capability that can cause a trading side effect.
- Trading remains authoritative for deciding which internal evidence is eligible for this public research projection.

### Network boundary

- Content Agent discovers the capability through Agent Network rather than hard-coding a Trading endpoint.
- Work is governed by Agent Network task proposal/contract semantics; returned research is an `ArtifactManifest`/`ArtifactRef`, not an arbitrary direct function response.
- Artifact bytes are materialized through Agent Network's signed/hash-verified artifact mechanism. Network receipts/evidence remain part of provenance.
- Seed Agents provide discovery and Evidence; Content Agent and Trading communicate through the existing A2A task path. No new cross-agent protocol is introduced.

### Consumer side — Content Agent

- Add a `TradingResearchNetworkSource` adapter that depends on Agent Network contracts/ports only and never imports `trading`.
- The adapter validates capability identity, artifact media type, schema version, producer identity, content hash, payload schema, and the absence of forbidden private/control fields.
- Valid research is converted into evidenced topic material / provenance references for the Content Agent. It is not automatically publishable.
- Any `ContentArtifact` generated from Network research still starts with `public_safe=False`; public admission remains an explicit Content Agent step.
- Network failure, missing evidence, schema mismatch, or unavailable Trading is represented as unavailable/unknown, never as a successful empty result.

Acceptance: end-to-end fixture proves Network discovery → task → verified artifact materialization → Content Agent ingestion without direct Trading imports or Trading database/API access.

## Phase 4 — migrate public Trading presentation

- Recreate useful public Trading material as Content Agent research/performance content sourced through the Network capability.
- Keep Trading developer/ops dashboards private only.
- Move subscription/product explanations only when their ownership is explicitly decided; payment/runtime control must not be casually coupled to content rendering.
- After parity and verification, remove Trading public exposure rather than maintaining duplicate public sites.

## Phase 5 — deployment and monetization

- Build the Website channel as a standalone static artifact.
- Deploy it independently from Trading; initially it may be served from the existing Ubuntu host on a separate root/host, later moved behind the chosen domain/CDN/hosting provider without changing the content core.
- Add domain, DNS, mailbox, Search Console, analytics, consent configuration where required, and AdSense identifiers when external values are available.
- Never put publisher IDs, credentials, domain assumptions, Agent Network private keys, or environment-specific secrets in source code.

## Phase 6 — additional channels

Add channel adapters (WeChat, X, Zhihu, newsletter, etc.) behind the same canonical content object. Each adapter owns formatting and publishing semantics; Content Agent core owns topic/evidence/content decisions and learning.

## Rename strategy

The product identity changes now to **Content Agent**. The GitHub repository name, Python distribution name, import package, and CLI remain temporarily compatible with `wechat-traffic-agent` / `wechat_traffic_agent` to avoid breaking Runtime consumers. Rename them only after compatibility aliases and deployment migration are tested.

## Hard invariants

1. Website is a channel, not the core domain model.
2. No public channel may publish an artifact unless `public_safe=True`.
3. Content from another domain keeps provenance; generated prose must not invent source facts.
4. A compromised public website must not gain Trading execution authority.
5. Content Agent never imports Trading implementation code or reads Trading storage directly.
6. Trading research reaches Content Agent through Agent Network discovery/task/artifact semantics.
7. Missing evidence is unknown, not zero or PASS.
8. Website/WeChat performance signals may inform content strategy but cannot mutate Trading policy.
9. Deployment is reversible; do not cut over the current Trading endpoint until the Content site passes independent smoke tests.
