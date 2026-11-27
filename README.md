# DSX OS Project Tracker

A static dashboard tracking the open source projects in [github.com/dsx-ai-factory](https://github.com/dsx-ai-factory), plus [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo): stars, forks, PRs, issues, releases, contributors, and commit activity, with a news feed triaged by Claude.

Live site: https://teb510.github.io/dsx-os-tracker/

## How it works

A GitHub Actions workflow (`.github/workflows/update.yml`) runs daily at 06:17 UTC, and on demand from the Actions tab:

1. `scripts/collect_github.py` pulls every public, non-fork repo in the configured orgs plus `extra_repos`, writes `site/data/repos.json`, and appends a daily snapshot to `site/data/history.json`.
2. `scripts/collect_news.py` searches Google News, Hacker News, and NVIDIA blog feeds, and merges new mentions into `site/data/news.json`.
3. `scripts/classify_news.py` sends unclassified mentions to Claude, which marks each as relevant or not, rates importance (high, medium, low), picks a category, and tags related repos. It skips cleanly without an `ANTHROPIC_API_KEY` secret.
4. `scripts/collect_work.py` gathers each repo's open PRs, PRs merged in the last 60 days, active issues, milestones, and any ROADMAP file, skipping bots and dependency bumps.
5. `scripts/classify_features.py` has Claude group that activity into features, size each one (XXL, XL, L, M, S), and place it on a roadmap: shipped, in progress, planned, or proposed. The previous roadmap is fed back so feature names stay stable, and a repo is reclassified at most once every `features_refresh_hours`.
6. The data is committed back to the repo and `site/` is deployed to GitHub Pages.

Pushes to `main` redeploy the site without re-collecting.

## Notes on the numbers

- **Star trends** come from the daily snapshots. GitHub no longer serves stargazer lists, so history starts on the first run.
- **NVIDIA vs external contributors** is a heuristic over the last 90 days of commit and PR authors: org members, a company field containing "nvidia", or an @nvidia.com commit email count as NVIDIA. Bots are excluded.
- **Contributor organizations** come from a non-personal commit email domain, else the GitHub company field. Spellings are merged ("redhat.com", "Red Hat"), and acquired companies listed in `internal_org_aliases` count as NVIDIA. External contributors with neither signal count as individuals.
- **Feature sizes and roadmap status** are Claude's judgment from PR and issue activity, not an official roadmap.
- **External contributors, 90d** in the header is summed per repo, so someone active in two repos counts twice.

## Configuration

Edit `config.json` to add orgs or repos, exclude repos, or change news queries and the classifier model. To drop news mentions containing certain terms, set the `NEWS_EXCLUDE_TERMS` secret to a comma-separated list.

## Running locally

```sh
pip install -r requirements.txt
python scripts/collect_github.py   # uses GITHUB_TOKEN or `gh auth token`
python scripts/collect_news.py
ANTHROPIC_API_KEY=... python scripts/classify_news.py
python -m http.server -d site 8000
```
