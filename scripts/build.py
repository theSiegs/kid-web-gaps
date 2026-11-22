"""Build gaps.txt: sites a child can reach that Chrome's own "block explicit sites" filter doesn't cover.

Sources: HaGeZi DNS blocklists (category lists, GPL-3.0) cut to what people actually visit using the Tranco
top-1M ranking, plus AI companion / character role-play chat sites found by name in the Tranco ranking (no
maintained list of those exists; every name hit goes through the review below). Then:
  - drop non-navigable infrastructure (DNS resolvers, CDNs, API hosts) and anything in overrides/never-block.txt
  - add overrides/always-block.txt
  - domains that are new since the last build go through an optional AI review (Gemini, if GEMINI_API_KEY is
    set): keep -> published, drop -> recorded in never-block.txt with the reason, review -> overrides/pending.txt
    for a human. Without a key, new domains wait in pending.txt; nothing new is published unreviewed.
Outputs gaps.txt (one domain per line) and gaps.json (domain -> category, rank, reason).

Run: python scripts/build.py            (needs only the standard library)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HAGEZI = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/{}-onlydomains.txt"
TRANCO = "https://tranco-list.eu/top-1m.csv.zip"
# category -> Tranco rank cut-off (None = keep the whole list)
CATEGORIES = {
    "nosafesearch": None,           # search engines that can't enforce SafeSearch
    "social": 10_000,
    "doh-vpn-proxy-bypass": 10_000,  # web proxies, VPNs: filter workarounds
    "anti.piracy": 10_000,           # piracy, sideload app stores, modded APKs
    "gambling.mini": 10_000,
}
# AI companion / character role-play chat: no maintained list exists, so candidates are Tranco domains (within
# this rank cut-off) whose name uses companion / role-play wording. The patterns over-match on purpose; the AI
# review (with its own question, see REVIEW_PROMPTS) sorts companion services from everything else.
AI_COMPANION = "ai_companion"
AI_COMPANION_CUT = 500_000
# Wording that points to a companion / role-play service wherever it appears in the name
AI_COMPANION_NAME = re.compile(
    r"waifu|husbando|role-?play|"
    r"(?:(?<![fh])ai|gpt|virtual|dream|my|hi)-?(?:girl|boy)friend|(?:girl|boy)friend-?(?:ai|gpt|chat|bot)|"
    r"(?:ai|gpt|virtual|dream|my)-?companion|companion-?(?:ai|chat|bot|gpt)|"
    r"(?:(?<![fh])ai|gpt)-?(?:gf|bf|chat|char|friend|lover|crush|dating|waifu|sex|nsfw)|"
    r"(?:gf|bf|chat|char|friend|lover|crush|dating|waifu|sex|nsfw|spicy|companion)-?ai(?![a-z])|"
    r"(?:gf|bf|friend|lover|crush|waifu|companion)-?gpt|"
    r"(?:nsfw|spicy|lewd|uncensored)-?(?:chat|char|bot)|"
    r"(?:^|[.-])rp-?(?:chat|bot)|"
    r"characters?-?(?:ai|chat|bot)|(?:ai|my)-?characters?|char(?:ai|bot|chat)s?"
)
# Looser wording, only counted on the TLDs AI chat services use (matched against the name before the TLD)
AI_COMPANION_TLD = re.compile(r"\.(?:ai|chat|fm|bot)$")
AI_COMPANION_TLD_NAME = re.compile(
    r"chat|char|girl|boy|friend|companion|crush|lover|spicy|nsfw|waifu|dating|"
    r"(?:^|-)(?:ai|my)?[gb]f(?:-|$)|(?:^|-)rp(?:-|$)"
)
# Sent to the review although the name doesn't match: hosts user-made character bots next to general assistants
AI_COMPANION_EXTRA = {"poe.com"}
# General AI assistants: never candidates of this category (subdomains included), whatever a review would say
AI_ASSISTANTS = {
    "chatgpt.com", "openai.com", "gemini.google.com", "claude.ai", "anthropic.com", "copilot.microsoft.com",
    "perplexity.ai", "meta.ai", "grok.com", "x.ai",
}
MAX_DOMAINS = 2_000  # safety cap; Family Link list limits are unknown
INFRA = re.compile(
    r"(^|[.-])(cdn|static|api|edge|img|images|media|assets|dns|resolver|cloudfront|akamai|fbcdn|ytimg|gstatic|doh)([.-]|\d|$)"
)
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-flash-lite-latest"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "kid-web-gaps/1.0 (+github.com/theSiegs/kid-web-gaps)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def read_list(path: Path) -> dict[str, str]:
    """domain -> note, from a one-domain-per-line file with '#' notes."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        body, _, note = line.partition("#")
        dom = body.strip().lower()
        if dom:
            out[dom] = note.strip()
    return out


def write_list(path: Path, header: str, entries: dict[str, str]) -> None:
    lines = [header.rstrip("\n")] + [f"{d}  # {n}" if n else d for d, n in sorted(entries.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def header_of(path: Path) -> str:
    if not path.exists():
        return ""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#"))


REVIEW_INTRO = (
    "You review candidate domains for a parental-control block list pushed to a child's Google account "
    "(Family Link, enforced in Chrome). Only page navigations are filtered, so infrastructure hostnames are "
    "useless.\n"
)
# Review question per category; categories not listed here use "default"
REVIEW_PROMPTS = {
    "default": (
        "The list targets what Chrome's own 'block explicit sites' filter misses: search engines without "
        "SafeSearch, social networks, web proxies/VPNs used to bypass filters, piracy and sideload app stores, "
        "gambling.\n"
        "For each domain decide:\n"
        "- keep: a website a child could navigate to that clearly fits its category;\n"
        "- drop: not a navigable site (CDN, API, DNS resolver, ad/tracking host, enterprise or school VPN, "
        "developer tool) or a mainstream legitimate site misfiled in the category (news, sports scores, "
        "ticketing, government);\n"
        "- review: dual-use or unsure (e.g. general file hosting, educational sites).\n"
    ),
    AI_COMPANION: (
        "These domains were picked only because their name looks like an AI companion or AI role-play chat "
        "service, so many are false hits.\n"
        "For each domain decide:\n"
        "- keep: the site is an AI companion, AI girlfriend/boyfriend, or AI character / role-play chat service "
        "a child could open and use;\n"
        "- drop: anything else, e.g. a general AI assistant (ChatGPT, Gemini, Claude, Copilot, Perplexity and "
        "their clones), an AI developer tool or customer-support chatbot builder, a chat or dating site with "
        "real people, a role-play game server or forum, an image tool, or a business that just has 'ai', "
        "'chat' or 'character' in its name;\n"
        "- review: unsure what the site is, or it mixes user-made character bots with a general assistant.\n"
    ),
}


def gemini_review(items: list[dict]) -> dict[str, dict]:
    """Ask Gemini to classify new candidates. Returns domain -> {decision, reason}; {} without a key."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not items:
        return {}
    # One question per category group (REVIEW_PROMPTS), up to 100 domains per request
    groups: dict[str, list[dict]] = {}
    for i in items:
        groups.setdefault(i["category"] if i["category"] in REVIEW_PROMPTS else "default", []).append(i)
    batches = [(g, its[s:s + 100]) for g, its in groups.items() for s in range(0, len(its), 100)]
    results: dict[str, dict] = {}
    for group, batch in batches:
        prompt = (
            REVIEW_INTRO + REVIEW_PROMPTS[group]
            + "Give a short reason (max 12 words) for every decision.\n\n"
            + "\n".join(f"{i['domain']}\t{i['category']}\trank {i['rank'] or 'n/a'}" for i in batch)
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "domain": {"type": "STRING"},
                            "decision": {"type": "STRING", "enum": ["keep", "drop", "review"]},
                            "reason": {"type": "STRING"},
                        },
                        "required": ["domain", "decision", "reason"],
                    },
                },
            },
        }
        req = urllib.request.Request(
            GEMINI_URL.format(GEMINI_MODEL),
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            for row in json.loads(text):
                dom = str(row.get("domain", "")).strip().lower()
                if row.get("decision") in ("keep", "drop", "review") and dom:
                    results[dom] = {"decision": row["decision"], "reason": str(row.get("reason", ""))[:120]}
        except Exception as err:  # a failed review leaves the batch for a human
            print(f"AI review failed for a batch of {len(batch)}: {err}", file=sys.stderr)
    return results


def is_assistant(dom: str) -> bool:
    """dom is (a subdomain of) a general AI assistant in AI_ASSISTANTS."""
    parts = dom.split(".")
    return any(".".join(parts[i:]) in AI_ASSISTANTS for i in range(len(parts) - 1))


def ai_companion_candidates(rank: dict[str, int]) -> dict[str, int | None]:
    """Tranco domains within AI_COMPANION_CUT whose name looks like an AI companion / role-play chat site."""
    out: dict[str, int | None] = {}
    for dom, r in rank.items():
        if r > AI_COMPANION_CUT:
            continue
        tld = AI_COMPANION_TLD.search(dom)
        if AI_COMPANION_NAME.search(dom) or (tld and AI_COMPANION_TLD_NAME.search(dom[:tld.start()])):
            out[dom] = r
    for dom in AI_COMPANION_EXTRA:
        out.setdefault(dom, rank.get(dom))
    return {d: r for d, r in out.items() if DOMAIN.match(d) and not INFRA.search(d) and not is_assistant(d)}


def main() -> int:
    never = read_list(ROOT / "overrides" / "never-block.txt")
    always = read_list(ROOT / "overrides" / "always-block.txt")
    pending = read_list(ROOT / "overrides" / "pending.txt")
    previous = read_list(ROOT / "gaps.txt")
    prev_meta = json.loads((ROOT / "gaps.json").read_text(encoding="utf-8")) if (ROOT / "gaps.json").exists() else {}

    rank: dict[str, int] = {}
    with zipfile.ZipFile(io.BytesIO(fetch(TRANCO))) as z:
        with z.open(z.namelist()[0]) as f:
            for r, d in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
                rank[d] = int(r)

    candidates: dict[str, dict] = {}
    for cat, cut in CATEGORIES.items():
        for line in fetch(HAGEZI.format(cat)).decode("utf-8", "replace").splitlines():
            dom = line.strip().lower()
            if not dom or dom.startswith("#") or not DOMAIN.match(dom):
                continue
            r = rank.get(dom)
            if cut is not None and (r is None or r > cut):
                continue
            if INFRA.search(dom) or dom in never:
                continue
            candidates.setdefault(dom, {"category": cat, "rank": r})
    for dom, r in ai_companion_candidates(rank).items():
        if dom not in never:
            candidates.setdefault(dom, {"category": AI_COMPANION, "rank": r})

    # New = not published last time and not already waiting for a human
    new = [
        {"domain": d, **m} for d, m in sorted(candidates.items())
        if d not in previous and d not in pending and d not in always
    ]
    review = gemini_review(new)

    published: dict[str, dict] = {}
    for dom, meta in candidates.items():
        # A domain a human already decided on (always-block) is published below, never sent back for review
        if dom in pending or dom in always:
            continue
        if dom in previous:
            published[dom] = {**meta, "reason": prev_meta.get(dom, {}).get("reason", "")}
            continue
        verdict = review.get(dom)
        if verdict is None:
            pending[dom] = f"new in {meta['category']}, not reviewed yet"
        elif verdict["decision"] == "keep":
            published[dom] = {**meta, "reason": f"AI: {verdict['reason']}"}
        elif verdict["decision"] == "drop":
            never[dom] = f"AI: {verdict['reason']}"
        else:
            pending[dom] = f"AI unsure: {verdict['reason']}"
    for dom, note in always.items():
        published[dom] = {"category": "always-block", "rank": rank.get(dom), "reason": note}
    # A pending domain that no longer appears in any source is dropped from pending
    # ... and so is one a human has since moved to always-block or never-block
    pending = {d: n for d, n in pending.items() if d in candidates and d not in always and d not in never}

    if len(published) > MAX_DOMAINS:
        keep = sorted(published, key=lambda d: (published[d]["category"] != "always-block", published[d]["rank"] or 10**9))
        published = {d: published[d] for d in keep[:MAX_DOMAINS]}

    today = dt.date.today().isoformat()
    added = sorted(set(published) - set(previous))
    removed = sorted(set(previous) - set(published))
    head = (
        f"# kid-web-gaps {today}: {len(published)} domains Chrome's explicit-site filter doesn't cover.\n"
        "# Built from HaGeZi DNS blocklists (GPL-3.0, github.com/hagezi/dns-blocklists) ranked with the Tranco list\n"
        "# (tranco-list.eu), plus reviewed AI companion / role-play chat sites found in the Tranco list.\n"
        "# License: GPL-3.0. Source and overrides: github.com/theSiegs/kid-web-gaps"
    )
    (ROOT / "gaps.txt").write_text(head + "\n" + "\n".join(sorted(published)) + "\n", encoding="utf-8", newline="\n")
    (ROOT / "gaps.json").write_text(json.dumps(dict(sorted(published.items())), indent=1) + "\n", encoding="utf-8", newline="\n")
    write_list(ROOT / "overrides" / "never-block.txt", header_of(ROOT / "overrides" / "never-block.txt"), never)
    write_list(ROOT / "overrides" / "pending.txt", header_of(ROOT / "overrides" / "pending.txt"), pending)

    summary = (
        f"{len(published)} published (+{len(added)} / -{len(removed)}), {len(pending)} pending review, "
        f"{len(new)} new candidates, AI reviewed {len(review)}"
    )
    print(summary)
    if out := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"### kid-web-gaps\n{summary}\n\nAdded: {', '.join(added[:50]) or '-'}\n\nRemoved: {', '.join(removed[:50]) or '-'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
