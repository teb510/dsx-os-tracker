"""Shared helpers: config, paths, HTTP, and GitHub API access (stdlib only)."""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"
USER_AGENT = "dsx-os-tracker (+https://github.com/teb510/dsx-os-tracker)"


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def load_config():
    return json.loads((ROOT / "config.json").read_text())


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    else:
        text = json.dumps(obj, indent=1, sort_keys=True)
    path.write_text(text + "\n")


def http(url, data=None, headers=None, retries=3, timeout=60):
    """GET (or POST when data is given). Returns (body_bytes, response_headers)."""
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            retryable = e.code in (429, 500, 502, 503, 504) or (
                e.code == 403 and "rate limit" in e.read().decode("utf-8", "ignore").lower()
            )
            if not retryable or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(5 * (attempt + 1))


def github_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Set GITHUB_TOKEN or log in with `gh auth login`.")


class GitHub:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {github_token()}",
            "Accept": "application/vnd.github+json",
        }

    def graphql(self, query, **variables):
        body = json.dumps({"query": query, "variables": variables}).encode()
        raw, _ = http("https://api.github.com/graphql", data=body, headers=self.headers)
        out = json.loads(raw)
        if out.get("errors"):
            raise RuntimeError(f"GraphQL error: {out['errors']}")
        return out["data"]

    def rest(self, path):
        raw, headers = http(f"https://api.github.com{path}", headers=self.headers)
        return (json.loads(raw) if raw else None), headers


# List prices per million tokens (input, output). Haiku's rate rises past 100K prompt tokens.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
WEB_SEARCH_PRICE = 0.01


class OverBudget(Exception):
    pass


class Claude:
    """Anthropic client that tallies spend and refuses calls once a per-run dollar cap is hit."""

    def __init__(self, cap_usd):
        import anthropic

        self.client = anthropic.Anthropic()
        self.cap = cap_usd
        self.spent = 0.0
        self.lock = threading.Lock()

    def create(self, model, **kwargs):
        with self.lock:
            if self.spent >= self.cap:
                raise OverBudget(f"spent ${self.spent:.3f} of ${self.cap:.3f}")
        if model.startswith("claude-haiku"):  # no server-side refusal fallback on Haiku
            response = self.client.messages.create(model=model, **kwargs)
        else:
            response = self.client.beta.messages.create(
                model=model, betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        u = response.usage
        rate_in, rate_out = PRICES.get(model, PRICES["claude-opus-5-5"])
        if model.startswith("claude-haiku") and u.input_tokens > 100_000:
            rate_in, rate_out = 0.50, 2.50
        searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
        cost = (u.input_tokens * rate_in + u.output_tokens * rate_out) / 1e6 + searches * WEB_SEARCH_PRICE
        with self.lock:
            self.spent += cost
        return response
