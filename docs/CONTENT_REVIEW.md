# Private draft review and explicit website admission

The generation worker only creates `public_safe=false` drafts. It cannot record
reviews, export admitted content, build a website, or publish. Network continues
to expose only the existing read-only Content status capability.

This interface is for a trusted local operator with filesystem access. A
`reviewer` value is an audit label, not authentication. Do not expose this CLI or
the private state directory as an unauthenticated public service. Reviews are
domain editorial records; they do not grant Runtime or Trading permissions.

## Inspect before deciding

Use the release's Python environment and source path. `CONTENT_ROOT` below is
the private `content` directory beneath the application's state root.

```bash
python -m wechat_traffic_agent.admission_cli --content-root "$CONTENT_ROOT" list
python -m wechat_traffic_agent.admission_cli --content-root "$CONTENT_ROOT" show "$FINGERPRINT"
python -m wechat_traffic_agent.admission_cli --content-root "$CONTENT_ROOT" review-template "$FINGERPRINT" > review.json
```

The template has no decision/reviewer/rationale and every check is UNKNOWN.
Inspect the original evidence referenced by the draft. Record a named reviewer,
a rationale, a timezone-aware review time, an APPROVE or REJECT decision, and
the results for provenance, factual support, privacy, originality, and
uncertainty. These are operator attestations, not automatic factual verification.
Every check must be PASS for approval. The review binds the complete immutable
draft's SHA256, its artifact ID, and its source fingerprint.

```bash
python -m wechat_traffic_agent.admission_cli --content-root "$CONTENT_ROOT" record-review --review review.json
python -m wechat_traffic_agent.admission_cli --content-root "$CONTENT_ROOT" export-approved "$FINGERPRINT"
```

Recording the same review or exporting the same approved content is idempotent
across restarts. Conflicting final decisions cannot overwrite one another.
REJECT is final for that immutable draft; a later content-revision workflow must
create a separately reviewable draft rather than rewrite this record. Revocation,
editorial revision and automated admission are not implemented by this interface.

The original stays private and unchanged. Export creates a separate admitted
`ContentArtifact` under `content/approved`, with private file permissions. It
does not copy anything into a served directory or execute a publication action.

## Build a candidate explicitly

The existing default build still contains only the specified starter artifacts.
To include admitted drafts, explicitly supply the private content root:

```bash
python -m wechat_traffic_agent.site_cli \
  --config site/production.example.json \
  --artifacts content/starter \
  --approved-content-root "$CONTENT_ROOT" \
  --output "$CANDIDATE_OUTPUT"
```

The builder revalidates reviews against original drafts, then creates public
projections in memory. It does not trust mutable export files. Pending/rejected
drafts are excluded; corrupted/stale reviews fail the build. Output must not
overlap private content state. Review records and private JSON never enter the
rendered website. Duplicate IDs/routes are rejected before rendering.

Deployment remains a separate operator action. Domain/HTTPS, public contact
email and AdSense publisher ID remain external launch inputs. A generated draft,
an approval, an export and a website candidate are four distinct states; none
implies that a page was published or that content performance improved.
