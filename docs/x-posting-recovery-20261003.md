# Ferro publication recovery — 2026-10-03
At 13:51 Asia/Shanghai, a browser cgroup OOM killed a renderer. The old publisher
and cognition pages stopped answering JavaScript although the browser endpoint
and account cookies remained available. A replacement X page subsequently
proved the authenticated handle was caini56006962.

The uncertain post from cycle v2-owner-5a1787438dff-1791004641 was already public:
https://x.com/caini56006962/status/2106259501913661750 .
Observation-only reconciliation recovered its receipt without composing or
submitting; verified daily progress advanced from 10 to 11.

Short posts now persist SUBMIT_PENDING before the click and serialize each exact
account/handle/text transaction. Interrupted and uncertain attempts can only
observe; a completed receipt is reused. An invalidated social model stage retains
its error and source evidence and no longer monopolizes backlog replay.

Agent Runtime now inserts and verifies the complete composer payload as one edit,
rather than retaining undo edits for each growing prefix. It records the completed
payload hash and reuses the matching receipt after a caller disconnect. A real
39,765-character request completed on the existing conversation; exact retry reused
its receipt. Browser sampled peak was 1,451,966,464 bytes with no new OOM.
This is a bounded recovery check, not proof of indefinite memory stability.

A concurrent release 6254a36 was deployed while this repair was being tested;
the fix must retain its learning-sharing changes when deployed.
