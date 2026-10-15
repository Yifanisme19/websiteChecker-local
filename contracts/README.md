# Web checker report contract

`web-checker-report.schema.json` is a pinned copy of:
`/Users/newpages/.openclaw/workspace-website-checker-agent/skills/web-checker/references/report.schema.json`

Contract ID: `web-checker-findings-v3` (stored in audit_reports.schema_version, never added to public JSON).
Source task: `01a0b8df-c72c-7ba2-91c6-649ef949fad6`, final user-approved four-field shape.

The skill exports public JSON from validated internal evidence. Our Worker validates
that delivered shape again, including exact checklist order, numeric statuses,
severity rules, issue numbering and ordering. It does not read arbitrary model-returned
paths, historical reports or internal evidence files. Shape validation cannot independently
prove observations, issue-to-check mappings, redirects or coverage claims; those remain
the skill's evidence/export responsibility.

Worker transport envelope (not public file contents):
- coverage_status: completed / partial / blocked / out_of_scope
- limitations: string array, required for non-completed coverage
- report: the exact public JSON, required even when coverage is blocked/out_of_scope

Every new public checklist has eight ordered rows. Status is exactly integer 1 or 0;
severity is low, medium or high, never empty. A row is FAIL (0) only when confirmed
reportable issues link to it, with the highest linked severity (Critical maps to high).
Otherwise it is PASS (1/low). Unresolved verification adds no finding, severity or FAIL.
PASS means no confirmed linked issue, not complete verification. True inspection states
and limitations remain private; blocked/out_of_scope reports have no issues and eight
PASS/low rows. Customer issues must describe confirmed defects, never inspection gaps.
Demo news 404s and empty visitor-counter values on *.newpages.xyz are excluded by the
skill before projection. The four-field public shape and rendered PASS/FAIL labels stay
unchanged. The v3 contract records the changed interpretation; historical binary-v2
reports remain readable with their original values and explanations.

Reports with blocked/partial coverage are retained as durable checkpoints, but they
do not finish the audit successfully. The Worker marks the job failed with
`audit_coverage_incomplete`, leaves the coverage metadata available for diagnosis,
and does not render/publish the customer HTML report. A retry can continue the audit.
Only `coverage_status=completed` becomes a completed job.

Existing reports retain their original contract and data. The v1 schema snapshot is
used only when reading stored web-checker-2026-09-19 reports, which may contain null
statuses and empty severities. New Worker output cannot use the legacy schema.

Contract changes must update this snapshot, validator tests, prompt and contract ID together.
