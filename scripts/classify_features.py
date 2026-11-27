"""Turn each repo's PRs, issues, and milestones into a sized feature roadmap with Claude.

Reads .cache/work.json (from collect_work.py) and writes site/data/features.json.
The previous run's features are fed back in so feature ids and titles stay stable
day to day. A repo is skipped when its input is unchanged or it was classified
within `refresh_hours`. Skips cleanly when ANTHROPIC_API_KEY is not set.
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import anthropic

from common import DATA, ROOT, Claude, OverBudget, load_config, log, read_json, write_json

NOW = datetime.now(timezone.utc)
SIZES = ["XXL", "XL", "L", "M", "S"]
STATUSES = ["shipped", "in_progress", "planned", "proposed"]

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "size": {"type": "string", "enum": SIZES},
                    "status": {"type": "string", "enum": STATUSES},
                    "theme": {"type": "string"},
                    "summary": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "integer"}},
                    "milestone": {"type": "string"},
                },
                "required": ["id", "title", "size", "status", "theme", "summary", "refs", "milestone"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "features"],
    "additionalProperties": False,
}

SYSTEM = """You build a product roadmap for one open source repository from its GitHub activity, for a dashboard that tracks NVIDIA's DSX AI Factory projects.

Input: the repo's description, open PRs, PRs merged in the last 60 days, active open issues, open milestones, an optional ROADMAP file, and the features you identified last time.

Group the activity into features: user-visible capabilities or significant engineering initiatives. One feature usually spans several PRs and issues. Leave out bug fixes, refactors, test-only changes, CI, docs, dependency bumps, and release chores unless they are part of a larger feature.

For each feature:
- id: short kebab-case slug. Reuse the previous id when it is the same feature.
- title: plain, specific name of the capability, at most 10 words (e.g. "Run control plane nodes as KubeVirt VMs"). Reuse the previous title unless it is now wrong.
- size, judged by impact on users and the project, not lines of code alone:
  - XXL: platform-defining. A new architecture, deployment model, or product surface that changes what the project can be used for (e.g. supporting VMs as control plane nodes in a Kubernetes engine). Usually multi-month and many PRs.
  - XL: a major capability: a new subsystem, a major integration, or a change many users will notice.
  - L: a significant feature within one area.
  - M: a moderate, self-contained enhancement.
  - S: a small improvement worth noting.
  Be strict: most repos have zero or one XXL. Do not inflate.
- status: "shipped" if the work merged in the last 60 days and nothing substantial is still open; "in_progress" if PRs are open or merging now; "planned" if there are issues or a milestone but little or no code yet; "proposed" for ideas, RFCs, or requests under discussion.
- theme: one or two words for the area (e.g. "Networking", "Scheduling", "Observability", "Security").
- summary: one plain sentence, at most 25 words, saying what it does and why it matters. No hype.
- refs: the PR and issue numbers that are evidence for it, most important first, at most 8.
- milestone: the milestone title if one applies, else "".

Also write summary: one or two plain sentences on where the project is heading, based on the biggest features.

Return at most 25 features, prioritizing the largest and most active. Shipped features should be ones that matter, not every merged change."""


def payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def enrich(features, work):
    """Attach titles, kinds, and dates to refs so the site needs nothing else."""
    index = {}
    for p in work["open_prs"]:
        index[p["number"]] = {"kind": "pr", "state": "open", "title": p["title"], "dates": [p["created"], p["updated"]]}
    for p in work["merged_prs"]:
        index[p["number"]] = {"kind": "pr", "state": "merged", "title": p["title"], "dates": [p["created"], p["merged"]]}
    for i in work["issues"]:
        index.setdefault(i["number"], {"kind": "issue", "state": "open", "title": i["title"], "dates": [i["created"], i["updated"]]})
    for f in features:
        refs, dates = [], []
        for n in f["refs"]:
            if n in index:
                ref = index[n]
                refs.append({"number": n, "title": ref["title"], "kind": ref["kind"], "state": ref["state"]})
                dates += ref["dates"]
        f["refs"] = refs
        f["milestone"] = f["milestone"] or None
        f["started"] = min(dates) if dates else None
        f["last_activity"] = max(dates) if dates else None
    return features


def classify(claude, cfg, repo, work, previous):
    payload = {
        "repo": repo["full_name"],
        "description": repo["description"],
        "milestones": work["milestones"],
        "roadmap_file": work["roadmap"],
        "open_prs": work["open_prs"],
        "merged_prs_last_60_days": work["merged_prs"],
        "open_issues": work["issues"],
    }
    prior = [{k: f[k] for k in ("id", "title", "size", "status")} for f in (previous or {}).get("features", [])]
    response = claude.create(
        cfg.get("features_model", cfg["model"]),
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": cfg.get("features_effort", "medium"), "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": json.dumps({**payload, "previous_features": prior}, ensure_ascii=False)}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    out = json.loads(text)
    u = response.usage
    log(f"  {repo['full_name']}: {len(out['features'])} features ({u.input_tokens} in, {u.output_tokens} out)")
    return {
        "name": repo["name"],
        "summary": out["summary"],
        "features": enrich(out["features"], work),
        "classified_at": NOW.isoformat(),
        "model": response.model,
        "input_hash": payload_hash(payload),
    }


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY not set; skipping feature classification")
        return
    cfg = load_config()["classifier"]
    refresh = timedelta(hours=cfg.get("features_refresh_hours", 20))
    work = read_json(ROOT / ".cache" / "work.json", {"repos": {}})["repos"]
    repos = {r["full_name"]: r for r in read_json(DATA / "repos.json", {"repos": []})["repos"]}
    out = read_json(DATA / "features.json", {"repos": {}})
    previous = out["repos"]

    todo = []
    for full, w in work.items():
        if full not in repos or not (w["open_prs"] or w["merged_prs"] or w["issues"]):
            continue
        prev = previous.get(full)
        if prev and NOW - datetime.fromisoformat(prev["classified_at"]) < refresh:
            continue
        todo.append(full)
    log(f"Classifying features for {len(todo)} repos")

    claude = Claude(cfg["budget_usd"]["features"])

    def one(full):
        try:
            return full, classify(claude, cfg, repos[full], work[full], previous.get(full))
        except (anthropic.APIError, OverBudget, RuntimeError, json.JSONDecodeError) as e:
            log(f"  {full}: {e}; keeping previous roadmap")
            return full, None

    with ThreadPoolExecutor(max_workers=cfg.get("features_workers", 6)) as pool:
        for full, result in pool.map(one, todo):
            if result:
                previous[full] = result

    log(f"Feature classification spent ${claude.spent:.3f}")
    # Drop repos that are no longer tracked.
    out["repos"] = {k: v for k, v in previous.items() if k in repos}
    out["generated_at"] = NOW.isoformat()
    write_json(DATA / "features.json", out)


if __name__ == "__main__":
    main()
