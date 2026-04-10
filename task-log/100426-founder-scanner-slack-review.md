# 100426 Task Log

## Founder Scanner / Notion

- Removed `confidence` from candidate output and Notion candidate writes.
- Increased generated evidence storage cap to 600 characters.
- Added cleaner evidence normalization so Slack display strips `Public evidence:` if it appears.
- Updated all Notion rows with `Status = Passed` to use researched, public-source evidence.
- Removed the `Public evidence:` prefix from existing Passed-row Notion evidence.
- Marked weak public evidence cases explicitly with `Needs enrichment` instead of inventing founder rationale.

## Slack Review Workflow

- Created Slack setup plan using bot token, app-level Socket Mode token, and channel ID.
- Added Slack posting for Notion candidates with `Status = New`.
- Added local Slack message mapping in `data/slack-candidates.json`.
- Added Socket Mode reaction listener in `scripts/slack_reactions.mjs`.
- Added `npm run slack:listen`.
- Added `@slack/bolt` dependency and lockfile.
- Added reaction-based Notion page status updates by exact Notion page ID.
- Added Slack message formatting with single-column candidate details, profile links, and emoji instructions.
- Tested Slack posting with Brock Whittaker from a `Passed` Notion row.

## Slack Message Copy / Formatting

- Replaced two-column Slack fields with a single-column layout.
- Removed noisy LinkedIn profile fragments such as follower and connection counts from message formatting.
- Changed Slack instructions to use actual emoji tokens.
- Switched reject instruction from `:x:` / `:red-x:` discussion to `:red_circle:` in the pushed workflow.
- Reworked Slack evidence display from raw profile text to a `Why reach out` style field backed by Notion Evidence.

## Public Evidence Enrichment

- Researched and updated evidence for 11 Passed candidates:
  - Jeremy Beltzer-Williams
  - D'Khari Q.
  - Arjun Pandey
  - Mishall A.
  - James Rattner
  - Andy Bonventre
  - Sirui Sun
  - Brock Whittaker
  - David Deng
  - Shyamal Hitesh Anadkat
  - Joowon Kim
- Used public sources such as personal sites, public LinkedIn pages, Stanford pages, company/job pages, public funding mentions, company filings, and project/profile pages.
- Avoided using existing Notion score/title/evidence fields as factual support for the rewritten evidence.

## GitHub / Repo

- Added and pushed commit `941db00` to `main`: `Add Slack review workflow for founder candidates`.
- Rebasing was required because GitHub `main` had advanced with `28fa3c7 Add Notion founder rescoring (#1)`.
- Resolved conflicts between the Slack workflow and Notion rescoring changes.
- Verified before push:
  - `python3 -m py_compile founder_scan.py`
  - `node --check scripts/slack_reactions.mjs`
  - `npm ls @slack/bolt --depth=0`
- Cleaned local working tree and cleared leftover stashes.
- Captured the GitHub workflow preference in this task log instead of personal Codex memory.

## Slack Review / Seed Expansion Follow-Up

- Fetched and merged `origin/main` at `b6d82d5`: `Add Slack rejection reason picklist (#2)`.
- Resolved merge conflicts by keeping the rejection-reason picklist and preserving the local Slack review behavior:
  - `:white_check_mark:` updates Notion `Status` to `Passed`.
  - `:red_circle:` updates Notion `Status` to `Rejected`.
  - `:eyes:` updates Notion `Status` to `Watchlist`.
  - rejected candidates trigger the Slack thread rejection-reason picker.
- Added `npm run slack:sync` to backfill reactions that were added while the Socket Mode listener was offline.
- Fixed Node `.env.local` loading so custom Slack env keys map to `SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN`, and `SLACK_CHANNEL_ID`.
- Added 40 fresh seed companies ahead of the original 30, focused on AI, devtools, infrastructure, fintech, compliance, and vertical SaaS.
- Restored transition-first query patterns and removed promotion-title scoring from the founder scanner.
- Ran an expanded search pass; wrote only Landon S. to Notion and pushed him to Slack. Rejected Alexey Kozy because `Building Cursor` referred to his current job, not a founder-transition signal.
- Ran Slack reaction sync after the listener missed an emoji event; Landon S. was updated to `Passed`.

## Working Notes

- Use task logs as the project memory source. Add only meaningful decisions, workflow lessons, data changes, and durable context that should guide future work.
- Do not use personal Codex memory for project-specific lessons when a task log entry is the better source of truth.
- Future GitHub changes should use a branch, PR, and auto-merge workflow by default. Do not push directly to `main` unless explicitly requested.
