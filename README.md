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
