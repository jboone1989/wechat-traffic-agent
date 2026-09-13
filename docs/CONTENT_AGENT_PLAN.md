# Content Agent — canonical product and migration plan

Status: ACTIVE  
Date: 2026-09-14  
Branch: `feat/content-agent-web`

## Goal

Evolve the current `wechat-traffic-agent` into a channel-independent Content Agent. The agent discovers worthwhile topics, consumes governed evidence from domain agents such as Trading, creates original content, learns from outcomes, and publishes through independent channel adapters.

The public website is the first production channel. WeChat remains a supported channel; it is no longer the product boundary.

## Target architecture

```text
Internet sources ─┐
                  ├─> Content Agent ─> Website ─> SEO / AdSense
Trading evidence ─┘        │
                           ├─> WeChat
                           └─> future channels
```

Trading owns trading research, experiments, execution, and private operations. It does not own public presentation. Content Agent must never require Trading database access, exchange credentials, trading controls, current positions, or private operational APIs.

## Phase 1 — foundation (current slice)

- Freeze the product boundary in ADR-0010.
- Add a platform-neutral `ContentArtifact` with provenance and an explicit `public_safe` gate.
- Add a static Website channel that refuses unsafe artifacts, escapes untrusted text, and produces index/article/research pages plus `robots.txt`, `sitemap.xml`, and optional AdSense metadata.
- Preserve all existing WeChat APIs and behavior; no big-bang package rename.
- Add focused tests for public-safety gating, HTML escaping, deterministic routes, and SEO files.

Acceptance: existing WeChat tests remain green; new website tests are green; no Trading dependency exists in the Content Agent package.

## Phase 2 — web-native content

- Add web-article generation using the existing topic discovery, Viral Radar, evidence, planner, and learning components.
- Keep `TietuDraft` as a WeChat-specific representation rather than the canonical content model.
- Add topic/archive pages, metadata, canonical URLs, structured data where justified, and production templates.
- Add channel-neutral performance observations so Website and WeChat can learn without mixing incompatible metrics.

## Phase 3 — Trading as a governed source

- Define a versioned `TradingContentEnvelope` produced by Trading itself.
- Envelope contains only delayed/sanitized research facts suitable for public use, provenance, limitations, and a stable evidence reference.
- Content Agent consumes the envelope read-only; it does not call Trading controls or inspect internal storage.
- Add contract tests on both sides. Publication still requires Content Agent `public_safe` admission.

## Phase 4 — migrate public Trading presentation

- Recreate useful public Trading material as Content Agent research/performance content.
- Keep Trading developer/ops dashboards private only.
- Move subscription/product explanations only when their ownership is explicitly decided; payment/runtime control must not be casually coupled to content rendering.
- After parity and verification, remove Trading public exposure rather than maintaining duplicate public sites.

## Phase 5 — deployment and monetization

- Build the Website channel as a standalone static artifact.
- Deploy it independently from Trading; initially this can be served from the existing Ubuntu host on a separate root/port, later moved behind the chosen domain/CDN/hosting provider without changing the content core.
- Add domain, DNS, mailbox, Search Console, analytics, and AdSense configuration when identifiers are available.
- Never put publisher IDs, credentials, domain assumptions, or environment-specific addresses in source code.

## Phase 6 — additional channels

Add channel adapters (WeChat, X, Zhihu, newsletter, etc.) behind the same canonical content object. Each adapter owns formatting and publishing semantics; the Content Agent core owns topic/evidence/content decisions and learning.

## Rename strategy

The product identity changes now to **Content Agent**. The GitHub repository name, Python distribution name, import package, and CLI remain temporarily compatible with `wechat-traffic-agent` / `wechat_traffic_agent` to avoid breaking Runtime consumers. Rename them only after compatibility aliases and deployment migration are tested.

## Hard invariants

1. Website is a channel, not the core domain model.
2. No public channel may publish an artifact unless `public_safe=True`.
3. Content from another domain keeps provenance; generated prose must not invent source facts.
4. A compromised public website must not gain Trading execution authority.
5. Missing evidence is unknown, not zero or PASS.
6. Website/WeChat performance signals may inform content strategy but cannot mutate Trading policy.
7. Deployment is reversible; do not cut over the current Trading endpoint until the Content site passes independent smoke tests.
