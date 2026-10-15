# 32 MiB payload fix — implementation / operations

## Changes
- Job-local `prepare-visual.py`: unmodified original evidence; vertical/horizontal tiling, bounded JPEG inspection copies (96 KiB maximum each), cumulative base64 budget 6 MiB; locked reservations count re-views too. Oversized selections request narrower crops; budget exhaustion requests checkpoint without altering ledger.
- Worker-internal checkpoint protocol: validates job/space/progress, native `sessions compact` (no destructive max-lines mode), requires `codex-app-server`, completed and not pending; archives previous image budget and resumes SAME session/TaskSpace from evidence. Failed/no-op/unknown compaction never clears budget. Twenty bounded continuations; public report schema unchanged.
- Relay patch: keep 32 MiB limits; decoded, wire and rewritten oversize get structured 413/payload_too_large, not 502. Chunked oversize gets a real response instead of destroying the request prematurely. Safe diagnostics contain stage/limit/observed wire size only, no model contents or secrets.
- Worker distinguishes deterministic payload failures and does not repeat them as transport failures.
- Preflight checks Pillow and active installed plugin source marker each request. Runtime upgrades that remove patch fail closed. Disk marker does not prove running code; maintenance procedure includes restart and real probes.

## Existing solution preflight
Installed OpenClaw/@openclaw/codex 2026.10.2. Downloaded and inspected official npm @openclaw/codex 2026.10.3 artifact without installing: same inference dispatch/proxy filenames, 32 MiB cap and generic 502 behavior. No applicable official fix was found there. Use already installed Pillow 12.2.0 and supported native compaction instead of a new image library or custom summarizer.

## Verification to date
- Application: 90 unittest tests pass (including checkpoint persistence/failure guards, normalized transport retry and nonretryable payload rejection).
- Image helper: 4 tests pass (tiling all regions, original preservation, bounds, cumulative reservations, crop validity and cross-job rejection).
- Skill report/evidence: 13 + 12 tests pass.
- Real installed relay candidate: 6 boundary/error tests pass with zero upstream requests; Gateway normalization retains status 413/invalid_request_error.
- Live Local native compaction probe e8b3a323-aa3e-45c4-bf0a-b9cf63d822a1: compacted:true; marker retained on same session.
- Live Local Worker checkpoint c295ada2-4187-4813-92c6-2811a2496bc9: native compaction confirmed, generation 1, archive retained, marker retained. Synthetic protocol test, no browser space claimed.
- Image-heavy Local audit dc1994ff-e763-4764-b090-778fa78a2ce2: FINISHED with HTTP 200 and coverage_status=completed, limitations=[] at 2026-10-15 10:06:29 MYT. Same TaskSpace34, five confirmed native compactions;31 pages,380 reserved inspection images across6 stages. Original evidence retained. Independent evidence/report validation passed and export matches. Not a customer submission; no database task/notification created.

## Deployment / rollback
Application + Skill: use `./manage prod update --version payload-guard-20261015 --apply` after managed services drain and API stops. Existing unrelated work is preserved in release backup and checksum manifest. Restore API and 3 workers in each environment after runtime verification.
Relay candidate/backups: Local `.runtime/relay-payload-guard-v4`. `python3 scripts/relay_payload_guard.py --apply <absolute-candidate-dir>` only after tests. Requires Gateway restart; original files and before/after SHA256 stored in manifest. Restore via `--restore` and another drained restart. Do not overwrite later plugin versions.

## Boundaries
This fix addresses the demonstrated oversized-image/request-body 502 mechanism, not every possible network/provider 502. A pathological text-only oversize or bypass of the helper still fails explicitly rather than becoming a successful incomplete report. Never claim complete inspection if image detail is unreadable. OpenClaw/plugin upgrades replace local package fixes; preflight intentionally refuses unverified replacements until reviewed.

### Live budget rollover evidence
2026-10-15 09:34:09 MYT: image-heavy Local audit returned internal checkpoint with HTTP 200.
Budget generation 0 retained 6,227,404 encoded bytes (below 6,291,456 cap).
09:35:39: supported native compaction confirmed; Worker continued the same audit.
Generation 1 started from zero; original progress/evidence and TaskSpace 34 retained.

### Application release staged in production
2026-10-15 09:42 MYT: `payload-guard-20261015` promoted with full backup manifest. Release tests passed. SHA256 verified all 66 application files and 20 skill files; no mismatches. Prod API restarted PID44709 and Local API PID44774. Worker pools remain stopped pending runtime patch activation. Relay files have not yet been applied/restarted.
09:41:45: second live native compaction confirmed; generation 2 resumed in the same TaskSpace.

### Live audit completion and installed relay
Six image stages encoded bytes: 6,227,404;6,258,936;6,284,984;6,206,812;6,285,468;1,652,476 (total32,916,080). All stages <=6,291,456. Final evidence has31 pages and24 findings, no coverage limitations; progress records finish_called=true, report_read_back=true. Driver final response resp_543f2857-c03b-46c5-9608-65bd4ec6f748. No original 502 observed in this full run.
2026-10-15 10:06 MYT: v4 relay patch applied with original-hash checks. Six boundary tests + Gateway normalization repeated against ACTUAL installed modules, passed with zero upstream calls. Local and Prod installed-guard preflight passed. Running Gateway PID43433 still needs graceful restart; both Worker pools remain paused pending restart+real probes.


## Final activation verification — 2026-10-15 10:09 MYT
- Initial detached activation FAILED: restart command returned `scheduled`, but the script checked the old PID immediately. PID49924 exited before probes; the failure was not treated as success. Retained original error/log. No repeated restart was issued.
- Gateway subsequently restarted at10:07:32 MYT, PID43433 →50382. Recovery independently verified both installed v4 SHA256 hashes, all66 production application hashes and20 Skill hashes, and current release `payload-guard-20261015`.
- Fresh Local and Prod Worker connection-only probes after restart both returned HTTP200 and `WORKER_CONNECTION_OK` (10:08:40 /10:08:39). Evidence: Local `.runtime/payload-tests/post-restart-probes.log`.
- Local API PID44774 and Prod API PID44709 healthy at `/websiteChecker/api/v1/health`, both status1. Local Worker PIDs49487–49489 and Prod49513–49515 all RUNNING, desired count3 per environment. Already running on recovery; no duplicate starts performed.
- Final live audit wrapper reports `finished`, coverage `completed`, no limitations; nested report exactly equals independently exported report. Existing31-page/five-compaction audit reused, not rerun.
- One-shot follow-up automation fa064a02-a5e7-4852-b973-62ca5962bffa disabled after inspection because this recovery completed its work. No unfinished children were listed.
- Verification command corrections: first hash check used `dist/` instead of actual `dist/.setup/`; corrected hashes match. First API health requests used `/api/health` and returned404; actual configured health path returned status1. These were verification-path mistakes, not runtime failures.
- Final authoritative activation state now `completed`; original failed activation retained separately as `activation-initial-error.json`.
