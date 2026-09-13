# ADR-0010 — Content Agent owns public content channels

Status: Accepted  
Date: 2026-09-14

## Context

The repository began as a WeChat 题图 agent, but its current capabilities already include internet-source ingestion, viral discovery, pattern analysis, content experiments, outcome observations, and strategy learning. Those capabilities are not inherently WeChat-specific.

Trading independently accumulated public HTML, showcase APIs, subscription surfaces, and disclosure code. That mixes a trading domain service with public presentation and increases the exposed surface of a system that should primarily own research and execution.

## Decision

The product represented by this repository is now **Content Agent**.

Content Agent owns:

- discovery of content opportunities from external sources;
- ingestion of explicitly public-safe evidence from other domain agents;
- channel-neutral content artifacts and provenance;
- content experiments and learning from channel outcomes;
- public Website rendering;
- WeChat rendering and future channel adapters.

Trading owns trading-domain research, evidence, experiments, execution, and private operations. Trading is a content source, not a public presentation host.

## Canonical boundary

A channel may only consume a `ContentArtifact` admitted as `public_safe=True`. Upstream domain data does not become publishable merely because it is readable.

Cross-agent integration is read-only and evidence-oriented:

```text
Domain Agent -> sanitized/versioned evidence -> Content Agent -> channel artifact
```

Content Agent must not depend on domain databases, trading credentials, current position details, operational controls, or write-capable domain APIs.

## Website semantics

Website is the first production channel. The initial implementation is a static build so public serving can be separated from Content Agent runtime and Trading runtime. Static output must escape untrusted text, provide deterministic URLs, SEO discovery files, and optional advertising configuration supplied at build time.

## WeChat compatibility

`TietuDraft`, current CLI behavior, and existing WeChat experiments remain supported. They become WeChat-specific adapters/representations rather than the canonical content object. Package/repository renames are staged to avoid breaking Runtime consumers.

## Security consequences

The public website has no authority over Trading. A later Trading source contract must be explicitly sanitized and versioned. Trading private dashboards may remain private for operations, but public SEO/AdSense presentation migrates to Content Agent before Trading public exposure is retired.

## Consequences

Positive: one content intelligence core can serve Website, WeChat, and future channels; Trading loses unnecessary public presentation responsibilities; SEO/AdSense work becomes independent of trading execution deployment.

Cost: a migration period keeps legacy names and duplicate presentation code. Public Trading surfaces are removed only after feature parity and independent deployment verification.
