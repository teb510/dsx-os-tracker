"""Gather what each repo is working on, as raw input for classify_features.py.

Writes .cache/work.json (not deployed): open PRs, PRs merged in the last 60 days,
active open issues, open milestones, and any ROADMAP file. Dependency bumps and
bot PRs are dropped here so the classifier only sees human feature work.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from common import DATA, ROOT, GitHub, log, read_json, write_json

NOW = datetime.now(timezone.utc)
MERGED_DAYS = 60
CACHE = ROOT / ".cache" / "work.json"

PR_FIELDS = """
number title isDraft createdAt updatedAt mergedAt additions deletions changedFiles bodyText
author { login ... on Bot { id } }
labels(first: 8) { nodes { name } }
milestone { title }
comments { totalCount }
"""

REPO_QUERY = f"""
query($owner: String!, $name: String!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    milestones(first: 10, states: OPEN, orderBy: {{field: DUE_DATE, direction: ASC}}) {{
      nodes {{ title description dueOn openIssues: issues(states: OPEN) {{ totalCount }} closedIssues: issues(states: CLOSED) {{ totalCount }} }}
    }}
    openPRs: pullRequests(first: 50, after: $after, states: OPEN, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {PR_FIELDS} }}
    }}
    recentIssues: issues(first: 50, states: OPEN, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      nodes {{ ...issue }}
    }}
    discussedIssues: issues(first: 25, states: OPEN, orderBy: {{field: COMMENTS, direction: DESC}}) {{
      nodes {{ ...issue }}
    }}
    roadmap1: object(expression: "HEAD:ROADMAP.md") {{ ... on Blob {{ text }} }}
    roadmap2: object(expression: "HEAD:docs/ROADMAP.md") {{ ... on Blob {{ text }} }}
    roadmap3: object(expression: "HEAD:docs/roadmap.md") {{ ... on Blob {{ text }} }}
  }}
}}
fragment issue on Issue {{
  number title createdAt updatedAt bodyText
  labels(first: 8) {{ nodes {{ name }} }}
  milestone {{ title }}
  comments {{ totalCount }}
  reactions {{ totalCount }}
}}
"""

OPEN_PRS_QUERY = f"""
query($owner: String!, $name: String!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    openPRs: pullRequests(first: 50, after: $after, states: OPEN, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {PR_FIELDS} }}
    }}
  }}
}}
"""

MERGED_QUERY = f"""
query($q: String!, $after: String) {{
  search(query: $q, type: ISSUE, first: 100, after: $after) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ ... on PullRequest {{ {PR_FIELDS} }} }}
  }}
}}
"""

NOISE = re.compile(r"^(chore|build|ci)\(deps[^)]*\)|^bump |^update dependency|^\[?automated\]?|^revert ", re.I)


def is_noise(pr):
    author = pr.get("author") or {}
    login = author.get("login") or ""
    return "id" in author or login.endswith("[bot]") or login.endswith("-bot") or bool(NOISE.search(pr["title"]))


def clip(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[: n - 1] + "…" if len(text) > n else text


def labels(node):
    return [l["name"] for l in node["labels"]["nodes"]]


def pr_entry(pr, with_body):
    out = {
        "number": pr["number"], "title": pr["title"], "labels": labels(pr),
        "size": pr["additions"] + pr["deletions"], "files": pr["changedFiles"],
        "created": pr["createdAt"][:10], "updated": pr["updatedAt"][:10],
    }
    if pr.get("mergedAt"):
        out["merged"] = pr["mergedAt"][:10]
    if pr.get("isDraft"):
        out["draft"] = True
    if pr.get("milestone"):
        out["milestone"] = pr["milestone"]["title"]
    if with_body:
        out["body"] = clip(pr["bodyText"], 400)
    return out


def prs(nodes, limit, bodies):
    """Keep human PRs; attach bodies to the largest few, where most feature detail lives."""
    kept = [p for p in nodes if p and not is_noise(p)][:limit]
    biggest = {p["number"] for p in sorted(kept, key=lambda p: -(p["additions"] + p["deletions"]))[:bodies]}
    return [pr_entry(p, p["number"] in biggest) for p in kept]


def collect_repo(gh, full):
    owner, name = full.split("/")
    d = gh.graphql(REPO_QUERY, owner=owner, name=name, after=None)["repository"]

    open_nodes = list(d["openPRs"]["nodes"])
    page = d["openPRs"]["pageInfo"]
    while page["hasNextPage"] and len(open_nodes) < 150:
        more = gh.graphql(OPEN_PRS_QUERY, owner=owner, name=name, after=page["endCursor"])["repository"]["openPRs"]
        open_nodes += more["nodes"]
        page = more["pageInfo"]

    since = (NOW - timedelta(days=MERGED_DAYS)).date().isoformat()
    q = f"repo:{full} is:pr is:merged merged:>={since} sort:updated-desc"
    merged_nodes, after = [], None
    while len(merged_nodes) < 300:
        res = gh.graphql(MERGED_QUERY, q=q, after=after)["search"]
        merged_nodes += res["nodes"]
        if not res["pageInfo"]["hasNextPage"]:
            break
        after = res["pageInfo"]["endCursor"]

    issues = {}
    for i in d["recentIssues"]["nodes"] + d["discussedIssues"]["nodes"]:
        issues[i["number"]] = {
            "number": i["number"], "title": i["title"], "labels": labels(i),
            "comments": i["comments"]["totalCount"], "reactions": i["reactions"]["totalCount"],
            "created": i["createdAt"][:10], "updated": i["updatedAt"][:10],
            "body": clip(i["bodyText"], 250),
            **({"milestone": i["milestone"]["title"]} if i["milestone"] else {}),
        }

    roadmap = next((d[k]["text"] for k in ("roadmap1", "roadmap2", "roadmap3") if d.get(k)), None)
    return {
        "milestones": [
            {"title": m["title"], "due": (m["dueOn"] or "")[:10] or None, "description": clip(m["description"], 300),
             "open": m["openIssues"]["totalCount"], "closed": m["closedIssues"]["totalCount"]}
            for m in d["milestones"]["nodes"]
        ],
        "open_prs": prs(open_nodes, 120, 40),
        "merged_prs": prs(merged_nodes, 250, 40),
        "issues": list(issues.values()),
        "roadmap": clip(roadmap, 6000) if roadmap else None,
    }


def main():
    repos = read_json(DATA / "repos.json", {"repos": []})["repos"]
    gh = GitHub()

    def one(r):
        try:
            return r["full_name"], collect_repo(gh, r["full_name"])
        except Exception as e:  # one bad repo shouldn't sink the run
            log(f"  {r['full_name']}: failed: {e}")
            return r["full_name"], None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(one, repos))
    work = {k: v for k, v in results.items() if v is not None}
    for k, v in work.items():
        log(f"  {k}: {len(v['open_prs'])} open, {len(v['merged_prs'])} merged, {len(v['issues'])} issues")
    write_json(CACHE, {"generated_at": NOW.isoformat(), "repos": work})


if __name__ == "__main__":
    main()
