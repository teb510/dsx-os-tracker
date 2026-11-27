"""Work out which organization external contributors work for.

Many people commit with a personal or no-reply email and leave their GitHub
company field blank. For those, gather public signals (profile, public orgs,
and whose repos they commit to elsewhere, via commit search) and have Claude
name the employer or say unknown. Without ANTHROPIC_API_KEY a simple heuristic
is used instead. Results are cached in site/data/affiliations.json and rechecked
after `affiliation_refresh_days`, so a daily run only looks up new people.
"""

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from common import DATA, log, read_json, write_json

NOW = datetime.now(timezone.utc)
CACHE = DATA / "affiliations.json"
SEARCH_PAUSE = 2.1  # commit search allows 30 requests a minute
NV_LOGIN = re.compile(r"(^nv-|-nv$|-nvidia$|^nvidia-|-nvda$)", re.I)
CONFIDENCE = ["high", "medium", "low", "unknown"]

SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "login": {"type": "string"},
                    "internal": {"type": "boolean"},
                    "org": {"type": "string"},
                    "domain": {"type": "string"},
                    "confidence": {"type": "string", "enum": CONFIDENCE},
                    "evidence": {"type": "string"},
                },
                "required": ["login", "internal", "org", "domain", "confidence", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}

SYSTEM = """You identify the employers of outside contributors to NVIDIA's open source DSX AI Factory projects, from public GitHub signals.

For each person you get their GitHub profile fields, public org memberships, commit email domains, and the organizations whose repositories they commit to most, with each org's display name, website, and sample repo names.

Return for each person:
- internal: true if they most likely work for NVIDIA or a company NVIDIA owns ({aliases}).
- org: the employer's common company name (e.g. "Red Hat", "Mirantis", "CoreWeave"), or "" if unknown or internal.
- domain: the employer's main web domain if known (e.g. "redhat.com"), else "".
- confidence: "high" for a corporate email domain or an explicit profile company; "medium" for repeated commits to one company's GitHub org or a clear bio; "low" for weak hints such as a login suffix; "unknown" when nothing points to an employer.
- evidence: a short phrase naming the deciding signal, e.g. "88 commits to Mirantis repos (Mirantis)". Never include an email address; a domain is fine.

Rules:
- An employer is a company, university, or lab, not a community. Foundations and community projects (kubernetes, kubernetes-sigs, cncf, apache, pytorch, and similar) and the person's own repos are not employers.
- Commits to NVIDIA's public open source orgs alone do not make someone an NVIDIA employee; many outside contributors work in them.
- Commits to a company's customer, production, or internal-tooling repos are strong evidence of employment there.
- Do not guess. Prefer "unknown" to a weak inference."""


def rest(gh, path):
    """GitHub REST call that waits out an exhausted rate limit instead of failing."""
    body, headers = gh.rest(path)
    if headers.get("X-RateLimit-Remaining") == "0":
        wait = max(1, int(headers.get("X-RateLimit-Reset", "0")) - int(time.time()) + 1)
        log(f"    rate limited; waiting {wait}s")
        time.sleep(min(wait, 900))
    return body


def org_profile(gh, owner, orgs):
    if owner not in orgs:
        try:
            u = rest(gh, f"/users/{owner}") or {}
            orgs[owner] = {"type": u.get("type"), "name": u.get("name") or owner, "blog": u.get("blog") or ""}
        except Exception:  # deleted or renamed accounts
            orgs[owner] = {"type": None, "name": owner, "blog": ""}
    return orgs[owner]


def signals(gh, login, emails, skip_owners, orgs):
    profile = rest(gh, f"/users/{login}") or {}
    member_of = [o["login"] for o in rest(gh, f"/users/{login}/orgs") or []]
    try:
        found = rest(gh, f"/search/commits?q=author:{login}&per_page=100") or {"items": []}
    except Exception:  # some accounts aren't searchable
        found = {"items": []}
    time.sleep(SEARCH_PAUSE)

    by_owner, repos, domains = Counter(), {}, Counter()
    for c in found["items"]:
        full = c["repository"]["full_name"]
        owner = full.split("/")[0]
        email = (c["commit"]["author"] or {}).get("email") or ""
        if "@" in email and not email.endswith("users.noreply.github.com"):
            domains[email.rsplit("@", 1)[1].lower()] += 1
        if owner.lower() == login.lower() or owner.lower() in skip_owners:
            continue
        by_owner[owner] += 1
        repos.setdefault(owner, set()).add(full.split("/")[1])

    commits_to = []
    for owner, n in by_owner.most_common(5):
        org = org_profile(gh, owner, orgs)
        if org["type"] == "Organization" and n >= 2:
            commits_to.append({"owner": owner, "name": org["name"], "website": org["blog"], "commits": n,
                               "sample_repos": sorted(repos[owner])[:4]})
    return {
        "login": login,
        "name": profile.get("name"),
        "company": profile.get("company"),
        "bio": profile.get("bio"),
        "blog": profile.get("blog"),
        "location": profile.get("location"),
        "public_orgs": member_of,
        "commit_email_domains": sorted({e.rsplit("@", 1)[1].lower() for e in emails if "@" in e} | set(domains)),
        "commits_to_orgs": commits_to,
    }


def heuristic(sig, aliases):
    from collect_github import PERSONAL_DOMAINS, org_key, org_of  # avoid a circular import at load

    if NV_LOGIN.search(sig["login"]):
        return {"internal": True, "org": "", "domain": "", "confidence": "low", "evidence": "NVIDIA-style login"}
    org = org_of(company=sig["company"])
    if org:
        internal = org_key(org) in aliases or "nvidia" in org.lower()
        return {"internal": internal, "org": "" if internal else org, "domain": "", "confidence": "high",
                "evidence": "GitHub profile company"}
    work = [d for d in sig["commit_email_domains"] if d not in PERSONAL_DOMAINS and not d.endswith(".local")]
    if work:
        internal = org_key(work[0]) in aliases
        return {"internal": internal, "org": "" if internal else work[0], "domain": work[0], "confidence": "medium",
                "evidence": f"commits from an @{work[0]} address"}
    top = sig["commits_to_orgs"][:1]
    total = sum(o["commits"] for o in sig["commits_to_orgs"])
    if top and top[0]["commits"] >= 5 and top[0]["commits"] * 2 >= total:
        o = top[0]
        domain = re.sub(r"^https?://(www\.)?", "", o["website"]).split("/")[0]
        return {"internal": False, "org": o["name"], "domain": domain, "confidence": "low",
                "evidence": f"{o['commits']} commits to {o['owner']} repos ({o['name']})"}
    return {"internal": False, "org": "", "domain": "", "confidence": "unknown", "evidence": ""}


def classify(batch, cfg, aliases):
    import anthropic  # only needed when a key is set

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=cfg["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM.format(aliases=", ".join(sorted(aliases))),
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    return {p.pop("login"): p for p in json.loads(text)["people"]}


def resolve(gh, candidates, cfg, aliases, skip_owners):
    """candidates: {login: set(commit emails)} for external people with no org yet.

    Returns the full cache of {login: {internal, org, domain, confidence, evidence, checked_at}}.
    """
    cache = read_json(CACHE, {"people": {}, "orgs": {}})
    refresh = timedelta(days=cfg.get("affiliation_refresh_days", 30))
    use_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))

    def is_stale(login):
        c = cache["people"].get(login)
        # Heuristic guesses get a proper look once a key is available.
        return (c is None or NOW - datetime.fromisoformat(c["checked_at"]) > refresh
                or (use_claude and c.get("method") == "heuristic"))

    stale = [l for l in candidates if is_stale(l)]
    stale = stale[: cfg.get("affiliation_max_lookups", 500)]
    log(f"Affiliations: {len(candidates)} unattributed, {len(stale)} to look up")

    sigs = []
    for i, login in enumerate(stale):
        try:
            sigs.append(signals(gh, login, candidates[login], skip_owners, cache["orgs"]))
        except Exception as e:  # one profile shouldn't sink the run
            log(f"    {login}: {e}")
        if i % 50 == 49:
            log(f"    {i + 1}/{len(stale)} looked up")

    results = {}
    for start in range(0, len(sigs), 25):
        batch = sigs[start:start + 25]
        if use_claude:
            try:
                results.update({k: {**v, "method": "claude"} for k, v in classify(batch, cfg, aliases).items()})
                continue
            except Exception as e:
                log(f"    batch {start // 25}: {e}; using heuristic")
        results.update({s["login"]: {**heuristic(s, aliases), "method": "heuristic"} for s in batch})

    for login, r in results.items():
        if login in candidates:
            cache["people"][login] = {**r, "checked_at": NOW.isoformat()}
    known = sum(1 for r in results.values() if r["internal"] or r["org"])
    log(f"Affiliations: resolved {known} of {len(results)} looked up")
    write_json(CACHE, cache)
    return cache["people"]
