"""Collect stats for every tracked repo and write site/data/{repos,history}.json.

GitHub no longer serves stargazer lists, so star trends come from the daily
snapshots in history.json rather than a backfilled timeline.
"""

import re
from datetime import datetime, timedelta, timezone

from common import DATA, GitHub, load_config, log, read_json, write_json

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
D30 = (NOW - timedelta(days=30)).date().isoformat()
D90 = NOW - timedelta(days=90)
WEEKS = 13
ALIASES = set()  # org_key()s that count as internal; filled from config in main()

REPO_QUERY = """
query($owner: String!, $name: String!, $q_pr_opened: String!, $q_pr_merged: String!,
      $q_issue_opened: String!, $q_issue_closed: String!, $since: GitTimestamp!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner name url description homepageUrl createdAt pushedAt isArchived
    stargazerCount forkCount
    watchers { totalCount }
    primaryLanguage { name color }
    licenseInfo { spdxId }
    repositoryTopics(first: 10) { nodes { topic { name } } }
    openIssues: issues(states: OPEN) { totalCount }
    closedIssues: issues(states: CLOSED) { totalCount }
    openPRs: pullRequests(states: OPEN) { totalCount }
    mergedPRs: pullRequests(states: MERGED) { totalCount }
    closedPRs: pullRequests(states: CLOSED) { totalCount }
    releases(first: 5, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes { name tagName url publishedAt isPrerelease }
    }
    recentPRs: pullRequests(first: 100, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number title url createdAt mergedAt state authorAssociation
        author { login ... on User { company } ... on Bot { id } }
      }
    }
    defaultBranchRef {
      name
      target {
        ... on Commit {
          history { totalCount }
          recent: history(since: $since) { totalCount }
        }
      }
    }
  }
  prOpened30: search(query: $q_pr_opened, type: ISSUE) { issueCount }
  prMerged30: search(query: $q_pr_merged, type: ISSUE) { issueCount }
  issueOpened30: search(query: $q_issue_opened, type: ISSUE) { issueCount }
  issueClosed30: search(query: $q_issue_closed, type: ISSUE) { issueCount }
}
"""

COMMITS_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { ... on Commit {
      history(since: $since, first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { committedDate author { email user { login company } } }
      }
    } } }
  }
}
"""

def list_repos(gh, cfg):
    names = []
    for org in cfg["orgs"]:
        page = 1
        while True:
            items, _ = gh.rest(f"/orgs/{org}/repos?type=public&per_page=100&page={page}")
            names += [r["full_name"] for r in items if not r["fork"]]
            if len(items) < 100:
                break
            page += 1
    names += cfg.get("extra_repos", [])
    excluded = set(cfg.get("exclude_repos", []))
    return sorted({n for n in names if n not in excluded}, key=str.lower)


def is_internal(hint, email=None, company=None, association=None, aliases=()):
    """Heuristic: org members/owners, company field, corporate email domain, or an acquired company."""
    if association in ("MEMBER", "OWNER"):
        return True
    if company and hint in company.lower():
        return True
    if email and email.lower().endswith(f"@{hint}.com"):
        return True
    org = org_of(email, company)
    return bool(org) and org_key(org) in aliases


# Personal mail providers and placeholders: an address here says nothing about an employer.
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com",
    "proton.me", "pm.me", "fastmail.com", "hey.com", "duck.com", "gmx.de", "gmx.net", "web.de",
    "mail.ru", "yandex.ru", "qq.com", "163.com", "126.com", "foxmail.com", "naver.com",
    "mail.com", "email.com", "engineer.com", "usa.com", "zoho.com", "tutanota.com", "seznam.cz",
    "users.noreply.github.com", "localhost",
}
GENERIC_TLDS = (".com", ".net", ".org", ".io", ".co", ".dev", ".cloud", ".tech")
NO_COMPANY = {"", "-", "none", "n/a", "na", "self", "self-employed", "freelance", "freelancer",
              "independent", "student", "personal", "home", "private", "me", "myself"}
COMPANY_SUFFIX = re.compile(r"[,.]?\s+(inc|inc\.|llc|ltd|ltd\.|gmbh|corp|corp\.|corporation|co\.|s\.a\.)$", re.I)


def org_of(email=None, company=None):
    """Best guess at an external contributor's organization, or None for individuals."""
    domain = (email or "").rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""
    if domain and domain not in PERSONAL_DOMAINS and not domain.endswith((".local", ".lan", ".internal")):
        return domain
    name = re.sub(r"\s+", " ", (company or "").strip().lstrip("@").strip())
    name = COMPANY_SUFFIX.sub("", name).strip()
    if name.lower() in NO_COMPANY:
        return None
    return name


def org_key(org):
    """Merge spellings of one org: "redhat.com", "Red Hat", and "@redhat" all become "redhat"."""
    org = org.lower()
    for tld in GENERIC_TLDS:
        if org.endswith(tld):
            org = org[: -len(tld)]
            break
    return re.sub(r"[^a-z0-9]", "", org)


def add_person(people, key, internal, email=None, company=None, n=1):
    p = people.setdefault(key, {"internal": False, "org": None, "n": 0})
    p["internal"] = p["internal"] or internal
    p["org"] = p["org"] or org_of(email, company)
    p["n"] += n


def org_breakdown(people):
    """Counts for external people: per organization, plus unaffiliated individuals."""
    orgs, solo = {}, {"people": 0, "contributions": 0}
    for p in people.values():
        if p["internal"]:
            continue
        if p["org"] is None:
            solo["people"] += 1
            solo["contributions"] += p["n"]
            continue
        o = orgs.setdefault(org_key(p["org"]), {"org": p["org"], "people": 0, "contributions": 0})
        # Prefer a written-out company name ("Red Hat") over a bare domain.
        if "." in o["org"] and "." not in p["org"]:
            o["org"] = p["org"]
        o["people"] += 1
        o["contributions"] += p["n"]
    ranked = sorted(orgs.values(), key=lambda o: (-o["people"], -o["contributions"], o["org"].lower()))
    return ranked, solo


def is_bot(login):
    return bool(login) and (login.endswith("[bot]") or login.endswith("-bot"))


def contributor_count(gh, full):
    items, headers = gh.rest(f"/repos/{full}/contributors?per_page=1&anon=0")
    link = headers.get("Link") or ""
    m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
    if m:
        return int(m.group(1))
    return len(items or [])


def commit_activity(gh, owner, name, hint):
    since = (NOW - timedelta(weeks=WEEKS)).isoformat()
    weekly = [0] * WEEKS
    authors = {}
    after = None
    while True:
        d = gh.graphql(COMMITS_QUERY, owner=owner, name=name, since=since, after=after)
        ref = d["repository"]["defaultBranchRef"]
        if not ref:
            break
        hist = ref["target"]["history"]
        for c in hist["nodes"]:
            ts = datetime.fromisoformat(c["committedDate"].replace("Z", "+00:00"))
            idx = WEEKS - 1 - min(WEEKS - 1, (NOW - ts).days // 7)
            weekly[idx] += 1
            a = c["author"] or {}
            user = a.get("user") or {}
            login = user.get("login")
            if is_bot(login) or (a.get("email") or "").endswith("[bot]@users.noreply.github.com"):
                continue
            if ts < D90:
                continue
            key = login or a.get("email")
            if key:
                internal = is_internal(hint, a.get("email"), user.get("company"), aliases=ALIASES)
                add_person(authors, key, internal, a.get("email"), user.get("company"))
        if not hist["pageInfo"]["hasNextPage"]:
            break
        after = hist["pageInfo"]["endCursor"]
    return weekly, authors


def collect_repo(gh, full, hint):
    owner, name = full.split("/")
    q = f"repo:{full}"
    d = gh.graphql(
        REPO_QUERY, owner=owner, name=name,
        q_pr_opened=f"{q} is:pr created:>={D30}",
        q_pr_merged=f"{q} is:pr is:merged merged:>={D30}",
        q_issue_opened=f"{q} is:issue created:>={D30}",
        q_issue_closed=f"{q} is:issue closed:>={D30}",
        since=(NOW - timedelta(days=30)).isoformat(),
    )
    r = d["repository"]
    branch = r["defaultBranchRef"] or {}
    target = branch.get("target") or {}

    people = {}
    external_prs_90 = 0
    for pr in r["recentPRs"]["nodes"]:
        created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
        author = pr["author"] or {}
        login = author.get("login")
        if created < D90 or not login or is_bot(login) or "id" in author:
            continue
        internal = is_internal(hint, company=author.get("company"), association=pr["authorAssociation"], aliases=ALIASES)
        add_person(people, login, internal, company=author.get("company"))
        if not internal:
            external_prs_90 += 1

    weekly, commit_authors = commit_activity(gh, owner, name, hint)
    for key, p in commit_authors.items():
        add_person(people, key, p["internal"], company=None, n=p["n"])
        people[key]["org"] = people[key]["org"] or p["org"]
    active_internal = sum(1 for p in people.values() if p["internal"])
    external_orgs, external_individuals = org_breakdown(people)

    latest = next((x for x in r["releases"]["nodes"] if x["publishedAt"]), None)

    return {
        "full_name": r["nameWithOwner"],
        "name": r["name"],
        "owner": owner,
        "url": r["url"],
        "description": r["description"] or "",
        "homepage": r["homepageUrl"] or "",
        "created_at": r["createdAt"],
        "pushed_at": r["pushedAt"],
        "archived": r["isArchived"],
        "language": (r["primaryLanguage"] or {}).get("name"),
        "language_color": (r["primaryLanguage"] or {}).get("color"),
        "license": (r["licenseInfo"] or {}).get("spdxId"),
        "topics": [t["topic"]["name"] for t in r["repositoryTopics"]["nodes"]],
        "stars": r["stargazerCount"],
        "forks": r["forkCount"],
        "watchers": r["watchers"]["totalCount"],
        "open_issues": r["openIssues"]["totalCount"],
        "closed_issues": r["closedIssues"]["totalCount"],
        "open_prs": r["openPRs"]["totalCount"],
        "merged_prs": r["mergedPRs"]["totalCount"],
        "closed_prs": r["closedPRs"]["totalCount"],
        "prs_opened_30d": d["prOpened30"]["issueCount"],
        "prs_merged_30d": d["prMerged30"]["issueCount"],
        "issues_opened_30d": d["issueOpened30"]["issueCount"],
        "issues_closed_30d": d["issueClosed30"]["issueCount"],
        "releases": r["releases"]["totalCount"],
        "latest_release": latest,
        "recent_releases": [x for x in r["releases"]["nodes"] if x["publishedAt"]],
        "default_branch": branch.get("name"),
        "commits_total": (target.get("history") or {}).get("totalCount", 0),
        "commits_30d": (target.get("recent") or {}).get("totalCount", 0),
        "commits_weekly": weekly,
        "contributors": contributor_count(gh, full),
        "active_contributors_90d": len(people),
        "active_internal_90d": active_internal,
        "active_external_90d": len(people) - active_internal,
        "external_prs_90d": external_prs_90,
        "external_orgs": external_orgs,
        "external_individuals": external_individuals,
        "_people": people,
    }


def delta(history, full, current, days):
    """Change since the oldest snapshot within `days` days, or None without history."""
    cutoff = (NOW - timedelta(days=days)).date().isoformat()
    past = [d for d in sorted(history) if cutoff <= d < TODAY and full in history[d]["repos"]]
    if not past:
        return None
    return current - history[past[0]]["repos"][full]["stars"]


def main():
    cfg = load_config()
    hint = cfg.get("internal_org_hint", "nvidia").lower()
    ALIASES.update(org_key(a) for a in cfg.get("internal_org_aliases", []))
    gh = GitHub()
    names = list_repos(gh, cfg)
    log(f"Tracking {len(names)} repos")

    repos = []
    for full in names:
        log(f"  {full}")
        try:
            repos.append(collect_repo(gh, full, hint))
        except Exception as e:  # keep going; one bad repo shouldn't sink the run
            log(f"    failed: {e}")

    # Dedupe people across repos for the org-level view.
    everyone = {}
    for r in repos:
        for key, p in r.pop("_people").items():
            add_person(everyone, key, p["internal"], n=p["n"])
            everyone[key]["org"] = everyone[key]["org"] or p["org"]
            if not p["internal"] and p["org"]:
                everyone[key].setdefault("repos", set()).add(r["name"])
    orgs, solo = org_breakdown(everyone)
    for o in orgs:
        o["repos"] = sorted({n for p in everyone.values()
                             if not p["internal"] and p["org"] and org_key(p["org"]) == org_key(o["org"])
                             for n in p.get("repos", ())})
    contributor_orgs = {
        "external_people": sum(1 for p in everyone.values() if not p["internal"]),
        "individuals": solo,
        "orgs": orgs,
    }

    history = read_json(DATA / "history.json", {})
    for r in repos:
        r["stars_7d"] = delta(history, r["full_name"], r["stars"], 7)
        r["stars_30d"] = delta(history, r["full_name"], r["stars"], 30)

    keys = ["stars", "forks", "open_prs", "merged_prs", "open_issues", "contributors",
            "prs_merged_30d", "commits_30d", "active_external_90d", "releases"]
    totals = {k: sum(r[k] for r in repos) for k in keys}
    totals["repos"] = len(repos)
    totals["contributor_orgs"] = contributor_orgs
    for k in ("stars_7d", "stars_30d"):
        vals = [r[k] for r in repos if r[k] is not None]
        totals[k] = sum(vals) if vals else None

    write_json(DATA / "repos.json", {"generated_at": NOW.isoformat(), "totals": totals, "repos": repos})
    history[TODAY] = {
        "totals": {k: totals[k] for k in keys},
        "repos": {r["full_name"]: {k: r[k] for k in keys} for r in repos},
    }
    write_json(DATA / "history.json", history, compact=True)
    log(f"Wrote {len(repos)} repos; {totals['stars']} stars total")


if __name__ == "__main__":
    main()
