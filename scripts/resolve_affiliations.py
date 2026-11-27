"""Work out which organization external contributors work for.

Many people commit with a personal or no-reply email and leave their GitHub
company field blank. For those, gather public signals (profile, public orgs,
and whose repos they commit to elsewhere, via commit search) and have Claude
name the employer or say unknown. Without ANTHROPIC_API_KEY a simple heuristic
is used instead. Results are cached in site/data/affiliations.json and rechecked
after `affiliation_refresh_days`, so a daily run only looks up new people.
"""

import fnmatch
import json
import os
import re
import time
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from common import DATA, Claude, log, read_json, write_json

NOW = datetime.now(timezone.utc)
CACHE = DATA / "affiliations.json"
SEARCH_PAUSE = 2.1  # commit search allows 30 requests a minute
NV_LOGIN = re.compile(r"(^nv-|-nv$|-nvidia$|^nvidia-|-nvda$)", re.I)
CONFIDENCE = ["high", "medium", "low", "unknown"]
VERSION = 2  # bump when the signals change so cached answers get one fresh look
WEB_VERSION = 2  # bump when the web prompt changes so unknowns get searched again

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
- NVIDIA employment: heavy, sustained activity in NVIDIA-owned repos (nvidia_org_commits is an exact count; hundreds of commits is heavy), or write access to NVIDIA repos (associations COLLABORATOR or MEMBER) with real commit volume, is good evidence: mark internal with medium confidence, unless a corporate email or profile company names another employer. A handful of PRs to one public NVIDIA project is not. Run:ai addresses and the run-ai GitHub org count as NVIDIA.
- Commits to a company's customer, production, or internal-tooling repos are strong evidence of employment there.
- Do not guess. Prefer "unknown" to a weak inference."""


WEB_SYSTEM = """You find the current employer of one open source contributor to NVIDIA's DSX AI Factory projects, using web search.

You get their GitHub login, name, profile fields, the repos they contribute to, and what GitHub activity already suggested.

Run these searches, in order, until one settles it:
1. "<name>" linkedin (add a work term such as Kubernetes, GPU, or AI infrastructure if the name is common)
2. "<login>" github "<name>"
3. "<name>" plus a project they contribute to
LinkedIn result snippets usually show the headline and current employer; that is enough. Personal sites, talk pages, and company blogs also count.

Accept a match when the name matches and the snippet fits their work (cloud, Kubernetes, GPU, AI or HPC infrastructure, the same location if known), or when a page links the GitHub login. Common names need the login or a project link. Report the current employer, not past ones. A company NVIDIA owns ({aliases}) counts as internal.

Use only what the searches return. Never answer from memory; if the searches don't show it, answer unknown.

End your reply with one JSON object on its own line:
{{"internal": false, "org": "", "domain": "", "confidence": "unknown", "evidence": ""}}
- org: the employer's common company name, or "" if unknown or internal.
- domain: the employer's main web domain if known, else "".
- confidence: "high" when a page ties the GitHub account to the employer; "medium" for a clear name, field, and location match; "low" for a plausible but thin match; "unknown" when you cannot tell.
- evidence: the source type and role, e.g. "LinkedIn: Staff Engineer at CoreWeave". No URLs and no email addresses."""



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


def is_nvidia_org(owner, patterns):
    return any(fnmatch.fnmatch(owner.lower(), p.lower()) for p in patterns)


def nvidia_commits(gh, login, patterns):
    """Exact count of the person's commits in NVIDIA's GitHub orgs, plus distinct repos."""
    named = [p for p in patterns if "*" not in p]
    q = "+".join([f"author:{login}"] + [f"org:{o}" for o in named])  # repeated org: qualifiers OR together
    try:
        res = rest(gh, f"/search/commits?q={q}&per_page=100") or {"total_count": 0, "items": []}
    except Exception:
        return 0, 0
    finally:
        time.sleep(SEARCH_PAUSE)
    return res.get("total_count", 0), len({c["repository"]["full_name"].lower() for c in res.get("items", [])})


def signals(gh, login, emails, skip_owners, orgs, nv_patterns=(), assoc=(), contributor=False):
    profile = rest(gh, f"/users/{login}") or {}
    member_of = [o["login"] for o in rest(gh, f"/users/{login}/orgs") or []]
    try:
        found = rest(gh, f"/search/commits?q=author:{login}&per_page=100") or {"items": []}
    except urllib.error.HTTPError as e:
        if e.code != 422:  # 422: the account isn't searchable. Anything else, retry next run.
            raise
        found = {"items": []}
    time.sleep(SEARCH_PAUSE)

    by_owner, repos, domains = Counter(), {}, Counter()
    nv_commits, nv_repos, others = 0, set(), 0
    for c in found["items"]:
        full = c["repository"]["full_name"]
        owner = full.split("/")[0]
        email = (c["commit"]["author"] or {}).get("email") or ""
        if "@" in email and not email.endswith("users.noreply.github.com"):
            domains[email.rsplit("@", 1)[1].lower()] += 1
        if owner.lower() == login.lower():
            continue
        others += 1
        if is_nvidia_org(owner, nv_patterns):
            nv_commits += 1
            nv_repos.add(full.lower())
        if owner.lower() in skip_owners:
            continue
        by_owner[owner] += 1
        repos.setdefault(owner, set()).add(full.split("/")[1])

    if contributor and nv_patterns:
        # The general search is capped at 100 relevance-ranked hits; ask NVIDIA's orgs directly.
        total = found.get("total_count", others) or others
        nv_commits, n_repos = nvidia_commits(gh, login, nv_patterns)
        nv_repos = set(range(n_repos))
        others = max(others, total)
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
        "nvidia_org_commits": nv_commits,
        "nvidia_org_share": round(nv_commits / others, 2) if others else 0,
        "nvidia_repos": len(nv_repos),
        "associations": sorted(assoc),
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
    nv = sig.get("nvidia_org_commits", 0)
    write_access = {"COLLABORATOR", "MEMBER"} & set(sig.get("associations", ()))
    if nv >= 100 or (write_access and nv >= 30):
        return {"internal": True, "org": "", "domain": "", "confidence": "low",
                "evidence": f"{nv} commits in NVIDIA GitHub orgs" + (" with write access" if write_access else "")}
    top = sig["commits_to_orgs"][:1]
    total = sum(o["commits"] for o in sig["commits_to_orgs"])
    if top and top[0]["commits"] >= 5 and top[0]["commits"] * 2 >= total:
        o = top[0]
        domain = re.sub(r"^https?://(www\.)?", "", o["website"]).split("/")[0]
        return {"internal": False, "org": o["name"], "domain": domain, "confidence": "low",
                "evidence": f"{o['commits']} commits to {o['owner']} repos ({o['name']})"}
    return {"internal": False, "org": "", "domain": "", "confidence": "unknown", "evidence": ""}


def classify(claude, batch, cfg, aliases):
    response = claude.create(
        cfg["model"],
        max_tokens=16000,
        system=SYSTEM.format(aliases=", ".join(sorted(aliases))),
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    return {p.pop("login"): p for p in json.loads(text)["people"]}


def web_lookup(claude, cfg, aliases, person):
    """One person, one web-searching Claude call. Returns (result, searches used)."""
    messages = [{"role": "user", "content": json.dumps(person, ensure_ascii=False)}]
    searches = 0
    for _ in range(3):  # resume server-side pauses a few times at most
        response = claude.create(
            cfg.get("affiliation_web_model", cfg["model"]),
            max_tokens=16000,
            system=WEB_SYSTEM.format(aliases=", ".join(sorted(aliases))),
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
            messages=messages,
        )
        use = getattr(response.usage, "server_tool_use", None)
        searches += getattr(use, "web_search_requests", 0) or 0
        if response.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": response.content}]
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    text = "\n".join(b.text for b in response.content if b.type == "text")
    found = re.findall(r"\{[^{}]*\}", text)
    if not found:
        raise RuntimeError("no JSON in reply")
    out = json.loads(found[-1])
    result = {
        "internal": bool(out.get("internal")),
        "org": str(out.get("org") or ""),
        "domain": str(out.get("domain") or ""),
        "confidence": out.get("confidence") if out.get("confidence") in CONFIDENCE else "unknown",
        "evidence": str(out.get("evidence") or ""),
    }
    if re.search(r"recollection|prior knowledge|from memory|unverified", result["evidence"], re.I):
        result.update(internal=False, org="", domain="", confidence="unknown")
    if result["org"] and result["evidence"] and not result["evidence"].lower().startswith("via web"):
        result["evidence"] = f"via web search: {result['evidence']}"
    return result, searches


def web_stage(gh, candidates, cache, cfg, aliases, refresh, claude):
    """Web-search the heaviest contributors that GitHub signals left unknown. Off unless configured."""
    def needs_web(login):
        c = cache["people"].get(login)
        if not c or c["internal"] or (c["org"] and c["confidence"] != "unknown"):
            return False
        checked = c.get("web_checked_at")
        return (checked is None or c.get("web_v") != WEB_VERSION
                or NOW - datetime.fromisoformat(checked) > refresh)

    todo = [l for l in sorted(candidates, key=lambda l: -candidates[l]["n"])
            if candidates[l]["n"] > 0 and needs_web(l)][: cfg.get("affiliation_web_lookups", 150)]
    log(f"Affiliations: web search for {len(todo)} contributors")
    if not todo:
        return
    def one(login):
        try:
            p = rest(gh, f"/users/{login}") or {}
            prior = cache["people"][login]
            person = {
                "login": login, "name": p.get("name"), "company": p.get("company"), "bio": p.get("bio"),
                "blog": p.get("blog"), "location": p.get("location"),
                "github_signals": prior.get("evidence") or "none", "contributions_90d": candidates[login]["n"],
                "repos": sorted(candidates[login].get("repos", ()))[:5],
            }
            return login, *web_lookup(claude, cfg, aliases, person)
        except Exception as e:  # one person shouldn't sink the run
            log(f"    {login}: {e}")
            return login, None, 0

    found = searches = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for login, result, n in pool.map(one, todo):
            searches += n
            if result is None:  # failed calls get retried next run
                continue
            entry = cache["people"][login]
            entry["web_checked_at"] = NOW.isoformat()
            entry["web_v"] = WEB_VERSION
            if result and (result["internal"] or (result["org"] and result["confidence"] != "unknown")):
                entry.update(result, method="web")
                found += 1
    log(f"Affiliations: web search placed {found} of {len(todo)} ({searches} searches)")


def canonical_names(cache):
    """Use the GitHub organization's own name when a person's domain spells its login (loft.sh, loft-sh).

    Claude and web pages sometimes use a company's former name; the org profile is current.
    """
    key = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    names = {key(login): o["name"] for login, o in cache["orgs"].items()
             if o.get("type") == "Organization" and key(o.get("name")) not in ("", key(login)) and len(key(login)) > 4}
    for p in cache["people"].values():
        name = names.get(key(p.get("domain")))
        if name and p.get("org") and p["org"] != name:
            p["org"] = name


def resolve(gh, candidates, cfg, aliases, skip_owners, nv_patterns=()):
    """candidates: {login: {"emails": set, "n": contributions}} for external people with no org yet.

    Returns the full cache of {login: {internal, org, domain, confidence, evidence, checked_at}}.
    """
    cache = read_json(CACHE, {"people": {}, "orgs": {}})
    refresh = timedelta(days=cfg.get("affiliation_refresh_days", 30))
    use_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))

    def is_stale(login):
        c = cache["people"].get(login)
        # Heuristic guesses get a proper look once a key is available.
        return (c is None or c.get("v") != VERSION or NOW - datetime.fromisoformat(c["checked_at"]) > refresh
                or (use_claude and c.get("method") == "heuristic"))

    # Heaviest contributors first; forkers (n = 0) after them.
    stale = [l for l in sorted(candidates, key=lambda l: -candidates[l]["n"]) if is_stale(l)]
    stale = stale[: cfg.get("affiliation_max_lookups", 500)]
    log(f"Affiliations: {len(candidates)} unattributed, {len(stale)} to look up")

    sigs = []
    for i, login in enumerate(stale):
        try:
            sigs.append(signals(gh, login, candidates[login]["emails"], skip_owners, cache["orgs"],
                                nv_patterns, candidates[login].get("assoc", ()), candidates[login]["n"] > 0))
        except Exception as e:  # one profile shouldn't sink the run
            log(f"    {login}: {e}")
        if i % 50 == 49:
            log(f"    {i + 1}/{len(stale)} looked up")

    claude = Claude(cfg["budget_usd"]["affiliations"]) if use_claude else None
    results = {}
    for start in range(0, len(sigs), 25):
        batch = sigs[start:start + 25]
        if use_claude:
            try:
                results.update({k: {**v, "method": "claude"} for k, v in classify(claude, batch, cfg, aliases).items()})
                continue
            except Exception as e:
                log(f"    batch {start // 25}: {e}; using heuristic")
        results.update({s["login"]: {**heuristic(s, aliases), "method": "heuristic"} for s in batch})

    for login, r in results.items():
        if login not in candidates:
            continue
        old = cache["people"].get(login, {})
        new = {**r, "checked_at": NOW.isoformat(), "v": VERSION}
        # Keep a recent web search answer rather than paying for the same search again.
        if not (new["internal"] or new["org"]) and old.get("web_checked_at"):
            new = {**old, "checked_at": new["checked_at"], "v": VERSION} if old.get("method") == "web" else {
                **new, "web_checked_at": old["web_checked_at"]}
        cache["people"][login] = new
    known = sum(1 for r in results.values() if r["internal"] or r["org"])
    log(f"Affiliations: resolved {known} of {len(results)} looked up")
    if use_claude and cfg.get("affiliation_web_lookups", 0) > 0:
        web_stage(gh, candidates, cache, cfg, aliases, refresh, claude)
    if claude:
        log(f"Affiliations: Claude spent ${claude.spent:.3f}")
    canonical_names(cache)
    write_json(CACHE, cache)
    return cache["people"]
