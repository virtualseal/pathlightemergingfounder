# Emerging Founder Scanner

Public-web MVP for surfacing high-potential future founders and writing the top candidates to Notion.

## Setup

Create `.env.local`:

```env
NOTION_TOKEN=secret_xxx
NOTION_DATABASE_ID=33ec925dab6a80029a3de6273506220a
EXA_API_KEY=your_exa_key
# Optional alternative/fallback:
BRAVE_SEARCH_API_KEY=your_brave_key
SLACK_BOT_TOKEN=xoxb-your_slack_bot_token
SLACK_APP_TOKEN=xapp-your_slack_socket_mode_token
SLACK_CHANNEL_ID=C012ABCDEF
```

Run:

```bash
python3 founder_scan.py --limit 10 --write-notion
```

Dry run without writing to Notion:

```bash
python3 founder_scan.py --limit 10
```

Force a provider:

```bash
python3 founder_scan.py --provider exa --limit 10
python3 founder_scan.py --provider brave --limit 10
```

Skip candidates already in Notion:

```bash
python3 founder_scan.py --provider exa --limit 10 --skip-existing-notion --write-notion
```

Preview updated Notion scores without writing changes:

```bash
python3 founder_scan.py --rescore-notion
```

Apply updated Notion scores:

```bash
python3 founder_scan.py --rescore-notion --apply-rescore
```

Send every Notion candidate with `Status = New` to Slack:

```bash
python3 founder_scan.py --send-new-slack
```

The Slack message includes the candidate details, evidence, and profile links at the end. After posting, the Notion row moves to `Pending Pathlight Response`. It only sends each Notion page once unless you force a resend:

```bash
python3 founder_scan.py --send-new-slack --resend-slack
```

Listen for Slack emoji reactions and update the Notion `Status`:

```bash
npm run slack:listen
```

Before using Slack rejection reasons, make sure the Notion review fields exist:

```bash
python3 founder_scan.py --ensure-review-fields
```

Sync reactions that were added while the listener was offline:

```bash
npm run slack:sync
```

By default, `:white_check_mark:` sets `Status` to `Passed`, `:red_circle:` sets `Status` to `Rejected`, and `:eyes:` sets `Status` to `Watchlist`. After a rejection, the Slack bot replies in-thread with a rejection reason picklist and writes the selected reason to Notion. Override the status and emoji names with:

```env
NOTION_APPROVE_STATUS=Passed
NOTION_REJECT_STATUS=Rejected
NOTION_WATCHLIST_STATUS=Watchlist
SLACK_APPROVE_EMOJI=white_check_mark
SLACK_REJECT_EMOJI=red_circle,red-x,x,red_x,negative_squared_cross_mark
SLACK_WATCHLIST_EMOJI=eyes
```

Credit-efficient discovery:

```bash
python3 founder_scan.py \
  --provider exa \
  --query-mode all \
  --max-queries 30 \
  --stop-after-candidates 15 \
  --per-company 2 \
  --limit 10 \
  --skip-existing-notion \
  --output-json data/candidates.json \
  --verbose
```

Use only high-intent transition queries:

```bash
python3 founder_scan.py --provider exa --query-mode transition --max-queries 20 --stop-after-candidates 10 --limit 10
```

Use only vesting-window queries for tier-3 source companies:

```bash
python3 founder_scan.py --provider exa --query-mode vesting --max-queries 20 --stop-after-candidates 10 --limit 10
```

Exa responses are cached in `data/exa-cache/` by default so scoring changes can be tested without re-spending credits. Use `--no-cache` to force fresh search results.

## Optional LinkedIn Validation

After Exa finds candidates, run a small Playwright-assisted validation pass against visible LinkedIn profiles:

```bash
npm install
npm run validate:linkedin -- --input data/candidates.json --output data/linkedin-validation.json --maxProfiles 10 --delayMs 12000 --timeoutMs 25000
```

The validator uses a persistent local browser profile at `.linkedin-browser-profile`. Log into LinkedIn in that browser once if prompted. It stops if LinkedIn shows login, checkpoint, security verification, or bot-protection text.

## How v1 Works

- Uses Exa or Brave Search APIs, not logged-in LinkedIn or Sales Navigator scraping.
- Searches for "ex-company", founder, stealth, and building signals.
- Scores candidates with transparent rules across function, source-company signal, founder language, recent departure, vesting window, promotion signal, tenure, and weak-fit penalties.
- Automatically scores rejected candidates as `0`.
- Writes candidates and evidence to Notion.

This is intentionally conservative. Strong candidates have public evidence URLs and profile text that supports the score.
