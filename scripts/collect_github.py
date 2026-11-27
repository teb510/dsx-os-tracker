"""Collect stats for every tracked repo and write site/data/{repos,history}.json.

GitHub no longer serves stargazer lists, so star trends come from the daily
snapshots in history.json rather than a backfilled timeline.
"""

import re
from datetime import datetime, timedelta, timezone

from common import DATA, GitHub, load_config, log, read_json, write_json
from resolve_affiliations import resolve

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
D30 = (NOW - timedelta(days=30)).date().isoformat()
D90 = NOW - timedelta(days=90)
WEEKS = 13
ALIASES = set()  # org_key()s that count as internal; filled from config in main()
MAX_PRS = 1000
MAX_FORKS = 200

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
      pageInfo { hasNextPage endCursor }
      nodes { ...pr }
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
fragment pr on PullRequest {
  createdAt authorAssociation
  author { login ... on User { company name } ... on Bot { id } }
  commits(first: 5) { nodes { commit { author { email user { login } } } } }
}
"""

PRS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    recentPRs: pullRequests(first: 100, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { ...pr }
    }
  }
}
fragment pr on PullRequest {
  createdAt authorAssociation
  author { login ... on User { company name } ... on Bot { id } }
  commits(first: 5) { nodes { commit { author { email user { login } } } } }
}
"""

COMMITS_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { ... on Commit {
      history(since: $since, first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { committedDate author { email user { login company name } } }
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


def add_person(people, key, internal, email=None, company=None, n=1, name=None, prs=0, assoc=None, via=None):
    p = people.setdefault(key, {"internal": False, "org": None, "n": 0, "prs": 0, "emails": set(), "name": None,
                                "assoc": set(), "via": None})
    if internal and not p["internal"]:
        p["via"] = via
    p["internal"] = p["internal"] or internal
    if assoc:
        p["assoc"].add(assoc)
    p["org"] = p["org"] or org_of(email, company)
    p["n"] += n
    p["prs"] += prs
    p["name"] = p["name"] or name
    if email:
        p["emails"].add(email)


def merge_person(people, key, p):
    add_person(people, key, p["internal"], n=p["n"], name=p["name"], prs=p["prs"], via=p["via"])
    people[key]["org"] = people[key]["org"] or p["org"]
    people[key]["emails"] |= p["emails"]
    people[key]["assoc"] |= p["assoc"]


RANK = {"high": 0, "medium": 1, "low": 2}


def org_breakdown(people, key_of=org_key, labels=None, weight="n"):
    """Per-organization counts for external people, plus the unattributed remainder."""
    labels = labels or {}
    orgs, solo = {}, {"people": 0, "contributions": 0}
    for p in people.values():
        if p["internal"]:
            continue
        if p["org"] is None:
            solo["people"] += 1
            solo["contributions"] += p[weight]
            continue
        k = key_of(p["org"])
        o = orgs.setdefault(k, {"org": labels.get(k, p["org"]), "people": 0, "contributions": 0, "confidence": "high"})
        # Prefer a written-out company name ("Red Hat") over a bare domain.
        if k not in labels and "." in o["org"] and "." not in p["org"]:
            o["org"] = p["org"]
        o["people"] += 1
        o["contributions"] += p[weight]
        if RANK.get(p.get("confidence", "high"), 0) > RANK[o["confidence"]]:
            o["confidence"] = p["confidence"]
    ranked = sorted(orgs.values(), key=lambda o: (-o["contributions"], -o["people"], o["org"].lower()))
    return ranked, solo


SERVICE_ACCOUNT = re.compile(r"(\[bot\]|-bot|-ops|-ci|-automation)$|^svc-", re.I)


def is_bot(login):
    """Bots and service accounts, which aren't people."""
    return bool(login) and bool(SERVICE_ACCOUNT.search(login))


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
                add_person(authors, key, internal, a.get("email"), user.get("company"), name=user.get("name"))
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

    # Page back through the 90-day window; busy repos open hundreds of PRs a month.
    prs = list(r["recentPRs"]["nodes"])
    page = r["recentPRs"]["pageInfo"]
    while page["hasNextPage"] and len(prs) < MAX_PRS and prs[-1]["createdAt"] >= D90.isoformat():
        more = gh.graphql(PRS_QUERY, owner=owner, name=name, after=page["endCursor"])["repository"]["recentPRs"]
        prs += more["nodes"]
        page = more["pageInfo"]

    people = {}
    for pr in prs:
        created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
        author = pr["author"] or {}
        login = author.get("login")
        if created < D90 or not login or is_bot(login) or "id" in author:
            continue
        internal = is_internal(hint, company=author.get("company"), association=pr["authorAssociation"], aliases=ALIASES)
        # Squash merges hide the author's email on the default branch; the PR's own commits keep it.
        emails = sorted({(c["commit"]["author"] or {}).get("email") or "" for c in pr["commits"]["nodes"]
                         if ((c["commit"]["author"] or {}).get("user") or {}).get("login") == login} - {""})
        emails = [e for e in emails if not e.endswith("users.noreply.github.com")]
        via = None
        if not internal and any(is_internal(hint, email=e, aliases=ALIASES) for e in emails):
            internal, via = True, "pr_email"
        add_person(people, login, internal, company=author.get("company"), name=author.get("name"), prs=1,
                   assoc=pr["authorAssociation"], via=via)
        for e in emails:
            add_person(people, login, False, email=e, n=0)

    weekly, commit_authors = commit_activity(gh, owner, name, hint)
    for key, p in commit_authors.items():
        merge_person(people, key, p)

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
        "_people": people,
        "_forks": recent_forks(gh, full),  # best effort; see recent_forks
    }


def recent_forks(gh, full):
    """Owners of forks made in the last 90 days, newest first, capped at MAX_FORKS."""
    out, page = [], 1
    while len(out) < MAX_FORKS:
        try:
            items, _ = gh.rest(f"/repos/{full}/forks?sort=newest&per_page=100&page={page}")
        except Exception as e:  # don't lose the repo's stats over its fork list
            log(f"    forks: {e}")
            break
        for f in items:
            if f["created_at"] < D90.isoformat():
                return out
            out.append({"login": f["owner"]["login"], "type": f["owner"]["type"]})
        if len(items) < 100:
            break
        page += 1
    return out[:MAX_FORKS]


def summarize(repos, people_by_repo, forks_by_repo, resolved, org_names):
    """Apply resolved affiliations, then write per-repo and overall org breakdowns."""
    # Every spelling of an org (resolved name, its domain) maps to one key and label.
    canon, labels = {}, {}
    for a in resolved.values():
        if a.get("org") and a.get("confidence") != "unknown":
            k = org_key(a["org"])
            labels.setdefault(k, a["org"])
            if a.get("domain"):
                canon[org_key(a["domain"])] = k

    def key_of(org):
        k = org_key(org)
        return canon.get(k, k)

    def apply(p, login):
        p.setdefault("confidence", "high" if p["org"] else "unknown")
        a = resolved.get(login)
        if a and not p["internal"] and p["org"] is None and a.get("confidence") != "unknown":
            if a["internal"]:
                p["internal"] = True
                p["via"] = "resolver"
            elif a.get("org"):
                p.update(org=a["org"], confidence=a["confidence"], evidence=a.get("evidence", ""))

    everyone, fork_people = {}, {}
    for r in repos:
        people = people_by_repo[r["full_name"]]
        for key, p in people.items():
            apply(p, key)
            if key not in everyone:
                everyone[key] = {**p, "emails": set(p["emails"]), "repos": set()}
            else:
                e = everyone[key]
                e["n"] += p["n"]
                if p["internal"] and not e["internal"]:
                    e["via"] = p.get("via")
                e["internal"] = e["internal"] or p["internal"]
                if e["org"] is None and p["org"]:
                    e.update(org=p["org"], confidence=p["confidence"], evidence=p.get("evidence", ""))
            if not p["internal"]:
                everyone[key]["repos"].add(r["name"])
        external = {k: p for k, p in people.items() if not p["internal"]}
        r["active_internal_90d"] = len(people) - len(external)
        r["active_external_90d"] = len(external)
        r["external_prs_90d"] = sum(p["prs"] for p in external.values())
        r["internal_contributions"] = sum(p["n"] for k, p in people.items() if p["internal"])
        r["external_orgs"], r["external_unattributed"] = org_breakdown(people, key_of, labels)

        # Forks: an org-owned fork names its org outright; a user's fork uses their affiliation.
        forkers = {}
        for f in forks_by_repo[r["full_name"]]:
            p = forkers.setdefault(f["login"], {"internal": False, "org": None, "n": 0})
            p["n"] += 1
            if f["type"] == "Organization":
                p.update(org=org_names.get(f["login"], f["login"]), confidence="high")
            else:
                apply(p, f["login"])
            fp = fork_people.setdefault(f["login"], {**p, "n": 0, "repos": set()})
            fp["n"] += 1
            fp["repos"].add(r["name"])
        fork_orgs, fork_solo = org_breakdown(forkers, key_of, labels)
        r["fork_orgs"] = [{"org": o["org"], "forks": o["contributions"], "confidence": o["confidence"]} for o in fork_orgs]
        r["forks_90d"] = sum(p["n"] for p in forkers.values())
        r["forks_internal_90d"] = sum(p["n"] for p in forkers.values() if p["internal"])
        r["forks_unattributed_90d"] = fork_solo["contributions"]

    def with_repos(orgs, people):
        for o in orgs:
            k = key_of(o["org"])
            o["repos"] = sorted({n for p in people.values() if not p["internal"] and p["org"] and key_of(p["org"]) == k
                                 for n in p["repos"]})
        return orgs

    orgs, solo = org_breakdown(everyone, key_of, labels)
    external = [(k, p) for k, p in everyone.items() if not p["internal"]]
    top = sorted((kp for kp in external if "@" not in kp[0]), key=lambda kp: -kp[1]["n"])[:60]
    fork_orgs, fork_solo = org_breakdown(fork_people, key_of, labels)
    return {
        "contributor_orgs": {
            # People who looked external but turned out to be NVIDIA, by how we found out.
            "reclassified_internal": {
                via: sum(1 for p in everyone.values() if p["internal"] and p.get("via") == via)
                for via in ("pr_email", "resolver")
            },
            "external_people": len(external),
            "external_contributions": sum(p["n"] for _, p in external),
            "unattributed": solo,
            "orgs": with_repos(orgs, everyone),
            "top_external": [
                {"login": k, "name": p["name"], "org": p["org"], "confidence": p["confidence"],
                 "evidence": p.get("evidence") or ("email domain or profile company" if p["org"] else ""),
                 "repos": sorted(p["repos"]), "contributions": p["n"]}
                for k, p in top
            ],
        },
        "fork_orgs": {
            "forks_90d": sum(p["n"] for p in fork_people.values()),
            "internal": sum(p["n"] for p in fork_people.values() if p["internal"]),
            "unattributed": fork_solo["contributions"],
            "orgs": [{"org": o["org"], "forks": o["contributions"], "people": o["people"], "repos": o["repos"],
                      "confidence": o["confidence"]} for o in with_repos(fork_orgs, fork_people)],
        },
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

    people_by_repo = {r["full_name"]: r.pop("_people") for r in repos}
    forks_by_repo = {r["full_name"]: r.pop("_forks") for r in repos}

    # Contributors first, then forkers, so lookups go to the people doing the work.
    candidates = {}
    for full, people in people_by_repo.items():
        for key, p in people.items():
            if not p["internal"] and p["org"] is None and "@" not in key:
                c = candidates.setdefault(key, {"emails": set(), "n": 0, "assoc": set(), "repos": set()})
                c["repos"].add(full)
                c["emails"] |= p["emails"]
                c["assoc"] |= p["assoc"]
                c["n"] += p["n"]
    for forks in forks_by_repo.values():
        for f in forks:
            if f["type"] == "User":
                candidates.setdefault(f["login"], {"emails": set(), "n": 0, "assoc": set()})
    skip = {o.lower() for o in cfg["orgs"]} | {x.split("/")[0].lower() for x in cfg.get("extra_repos", [])}
    resolved = resolve(gh, candidates, cfg.get("classifier", {}), ALIASES, skip, cfg.get("nvidia_github_orgs", []))
    org_names = {}
    for forks in forks_by_repo.values():
        for f in forks:
            if f["type"] == "Organization" and f["login"] not in org_names:
                try:
                    org_names[f["login"]] = (gh.rest(f"/users/{f['login']}")[0] or {}).get("name") or f["login"]
                except Exception:  # renamed or deleted account
                    org_names[f["login"]] = f["login"]
    breakdowns = summarize(repos, people_by_repo, forks_by_repo, resolved, org_names)

    history = read_json(DATA / "history.json", {})
    for r in repos:
        r["stars_7d"] = delta(history, r["full_name"], r["stars"], 7)
        r["stars_30d"] = delta(history, r["full_name"], r["stars"], 30)

    keys = ["stars", "forks", "open_prs", "merged_prs", "open_issues", "contributors",
            "prs_merged_30d", "commits_30d", "active_external_90d", "releases"]
    totals = {k: sum(r[k] for r in repos) for k in keys}
    totals["repos"] = len(repos)
    totals.update(breakdowns)
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
