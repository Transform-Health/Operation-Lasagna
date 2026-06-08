# CalAIM Policy Tracker

Automated CalAIM policy dashboard. Scans government sources, trade press, and RSS feeds daily and weekly, then updates the dashboard via GitHub Actions. Deployed as a static site on Netlify.

## Setup (one-time)

### 1. Create the GitHub repo
- Go to github.com → New repository
- Name it something like `calaim-tracker`
- Set to Public or Private (Netlify works with both)
- Upload all files from this folder

### 2. Add your Anthropic API key
- Go to console.anthropic.com → API Keys → Create key
- In your GitHub repo: Settings → Secrets and variables → Actions → New repository secret
- Name: `ANTHROPIC_API_KEY`
- Value: your API key

### 3. Connect Netlify
- Go to netlify.com → Add new site → Import from GitHub
- Select your repo
- Build settings: leave blank (no build command needed — it's a static HTML file)
- Publish directory: `/` (root)
- Deploy

Netlify will give you a URL like `https://your-site-name.netlify.app`. Every time GitHub Actions commits a new `data.json`, Netlify auto-deploys within ~30 seconds.

### 4. Test the workflow
- In your GitHub repo: Actions tab → CalAIM Policy Scan → Run workflow → select "weekly" → Run
- Check that `data.json` is updated in the repo after the run completes
- Check that your Netlify URL shows the new data

## How it works

```
RSS feeds + news pages  →  update_dashboard.py  →  data.json  →  Netlify
(fetched by GitHub Actions)   (calls Claude API)    (committed)   (auto-deploys)
```

**Daily scan (every day at 8am PT):** Checks for same-day items, adds them to the `dailyUpdates` section at the top of the dashboard.

**Weekly scan (Mondays at 8am PT):** Full 7-day scan. Archives the previous week's content, creates a new current version with fresh news and policy items.

## Updating the UI

The dashboard template is `index.html`. Data is in `data.json`. Edit `index.html` to change layout, add sections, or update the hardcoded action items and horizon dates. The Python script only touches `data.json` — it never modifies `index.html`.

## Files

```
├── index.html                          Dashboard UI (loads data.json)
├── data.json                           Scan results — auto-updated by Actions
├── scripts/
│   └── update_dashboard.py             Scan + update script
└── .github/
    └── workflows/
        └── calaim-scan.yml             GitHub Actions schedule
```

## Manual runs

You can trigger a scan manually from the Actions tab in GitHub → CalAIM Policy Scan → Run workflow. Use the dropdown to force a daily or weekly scan regardless of the day.
