"""Classify unclassified news items with Claude: relevance, importance, category, repos.

Skips cleanly when ANTHROPIC_API_KEY is not set, leaving items unclassified.
"""

import json
import os

import anthropic

from common import DATA, Claude, OverBudget, load_config, log, read_json, write_json

CATEGORIES = [
    "launch", "release", "partnership", "customer_adoption", "business",
    "technical_deep_dive", "tutorial", "analysis_opinion", "community", "other",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevant": {"type": "boolean"},
                    "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "repos": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["id", "relevant", "importance", "category", "repos", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = """You triage news for a dashboard that tracks NVIDIA's open source DSX OS / DSX AI Factory projects on GitHub.

Tracked projects (repo name: description):
{projects}

For each news item, decide:
- relevant: true only if the item is substantively about one or more tracked projects, or about NVIDIA DSX / DSX OS / DSX AI Factory as a whole. Generic NVIDIA stock, GPU, or AI-industry coverage that only mentions them in passing is not relevant. Matches on unrelated meanings of a word (for example "run AI" or another product called Dynamo) are not relevant.
- importance: "high" for launches, major releases, open-sourcing announcements, significant partnerships or customer adoption, or coverage likely to move the project's adoption; "medium" for solid technical or industry coverage; "low" for minor mentions, reposts, or thin content. Irrelevant items get "low".
- category: the best fit from the allowed list.
- repos: tracked repo names (exactly as listed, e.g. "nvke") the item is about; empty if none specifically.
- summary: one plain sentence, at most 25 words, saying what the item reports. No hype.

Return one entry per input item, echoing its id."""


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY not set; skipping news classification")
        return
    cfg = load_config()["classifier"]
    news = read_json(DATA / "news.json", {"items": []})
    repos = read_json(DATA / "repos.json", {"repos": []})["repos"]
    projects = "\n".join(f"- {r['name']}: {r['description'] or '(no description)'}" for r in repos)
    system = SYSTEM.format(projects=projects)
    known = {r["name"] for r in repos}

    todo = [n for n in news["items"] if "llm" not in n]
    log(f"Classifying {len(todo)} items")
    claude = Claude(cfg["budget_usd"]["news"])
    by_id = {n["id"]: n for n in news["items"]}
    size = cfg["batch_size"]

    for start in range(0, len(todo), size):
        batch = todo[start:start + size]
        payload = [
            {"id": n["id"], "title": n["title"], "source": n["source"],
             "published": n["published"], "url": n["url"], "snippet": n.get("snippet", "")}
            for n in batch
        ]
        try:
            response = claude.create(
                cfg["model"],
                max_tokens=16000,
                system=system,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
        except OverBudget as e:
            log(f"  budget reached ({e}); leaving the rest for tomorrow")
            break
        except anthropic.APIStatusError as e:
            log(f"  batch {start // size}: API error {e.status_code}; leaving unclassified")
            continue
        except anthropic.APIConnectionError as e:
            log(f"  batch {start // size}: connection error {e}; leaving unclassified")
            continue
        if response.stop_reason != "end_turn":
            log(f"  batch {start // size}: stop_reason={response.stop_reason}; leaving unclassified")
            continue
        text = next(b.text for b in response.content if b.type == "text")
        for result in json.loads(text)["items"]:
            n = by_id.get(result.pop("id"))
            if n is None:
                continue
            result["repos"] = [r for r in result["repos"] if r in known]
            result["model"] = response.model
            n["llm"] = result
        log(f"  batch {start // size}: {len(batch)} items")

    log(f"News classification spent ${claude.spent:.3f}")
    write_json(DATA / "news.json", news)


if __name__ == "__main__":
    main()
