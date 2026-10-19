# UAE Ledger

A one-page, self-updating news hub for UAE business and finance. Python standard library only.

Every 30 minutes a GitHub Action: reads ~28 feeds and public APIs, groups headlines about the same event, writes a short original blurb for each new story, checks feed health and duplicates, and publishes `site/index.html` to GitHub Pages. If too few feeds work or any check fails, nothing is published and the last good page stays up.

## Set up (about 5 minutes)
1. Create a new GitHub repo and push this folder to it.
2. Settings > Pages > Source: **GitHub Actions**.
3. Settings > Secrets and variables > Actions > New secret: `ANTHROPIC_API_KEY` (optional; without it stories show headlines only, no blurbs).
4. Actions tab > "Update site" > Run workflow. Check the run summary for the feed health table.

## Files
- `updater/feeds.json`: the feed list. Add, remove, or change outlet weights (higher weight = preferred lead source).
- `updater/hub.py`: the whole pipeline. `SITE_NAME` and `TAGLINE` are at the top.
- `tests/`: offline tests with fictional fixtures. Run `python3 -m unittest discover -s tests`.
- `data/summaries.json`: blurb cache so each story is summarized once.
- `demo/index.html`: sample page built from fictional data.

## Rules the pipeline follows
- Stores and shows only: headline, link, outlet name, time, and its own blurb. Feed snippets are never saved or displayed.
- A blurb is rejected if it repeats five words in a row from the source, sits too close to a headline, or uses a banned phrase or dash. It retries once, then falls back to headline only.
- Run locally: `ANTHROPIC_API_KEY=... python3 updater/hub.py`, then open `site/index.html`.
