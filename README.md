# kid-web-gaps

A daily-updated list of websites a child can reach that **Chrome's own "Try to block explicit sites" filter
doesn't cover**, sized for per-child browser block lists such as Google Family Link.

Chrome's filter (SafeSites) targets sexually explicit content, and Chrome forces SafeSearch on Google. This list
covers the gaps instead:

| Category | Source list | Selection |
|---|---|---|
| Search engines that can't enforce SafeSearch (unfiltered image search) | HaGeZi `nosafesearch` | all |
| Social networks | HaGeZi `social` | Tranco top 10k |
| Filter workarounds: web proxies, VPNs | HaGeZi `doh-vpn-proxy-bypass` | Tranco top 10k |
| Piracy, sideload app stores, modded APKs | HaGeZi `anti.piracy` | Tranco top 10k |
| Gambling | HaGeZi `gambling.mini` | Tranco top 10k |
| AI companion / character role-play chat (`ai_companion`) | Tranco domains whose name matches companion / role-play wording (no list exists) | Tranco top 500k, each one AI-reviewed |

No maintained AI-companion block list exists, so that category is discovered by name: Tranco domains containing
words such as *waifu*, *roleplay*, *ai girlfriend*, *companion*, *character ai*, or *chat* / *crush* / *spicy* on an
`.ai` / `.chat` / `.fm` / `.bot` domain. The patterns over-match on purpose (about a hundred names per run) and the
review below sorts companion services from false hits. General AI assistants (ChatGPT, Gemini, Claude, Copilot,
Perplexity, Meta AI, Grok) are excluded outright; well-known companion sites the patterns miss are in the AI section
of `overrides/always-block.txt`.

Popularity cut-offs keep the list to the few hundred domains people actually visit; the full category lists
(tens of thousands of domains) belong in DNS filtering, not in a browser block list.

## Use it

- **Plain list:** `https://raw.githubusercontent.com/theSiegs/kid-web-gaps/main/gaps.txt` (one domain per line,
  `#` comments). `gaps.json` has the category, Tranco rank and the reason for each domain.
- Each domain is meant to cover its subdomains (a Family Link domain entry `*.example.com`).
- Home Assistant users: the Google Family Link integration ([HAFamilyLink](https://github.com/noiwid/HAFamilyLink))
  is getting website-list support; a sync service can keep a child's blocked sites in step with this file.

## How it's built (daily, GitHub Actions)

1. Download the five HaGeZi category lists and the Tranco top-1M ranking.
2. Keep all of `nosafesearch` and the top-10k slice of the others; add the AI companion name matches from the
   Tranco top 500k (minus the general AI assistants).
3. Drop non-navigable infrastructure (DNS resolvers, CDNs, API hosts) and everything in
   `overrides/never-block.txt`; add everything in `overrides/always-block.txt`.
4. Domains that are **new** since the last build are reviewed by an AI model (Google Gemini, free tier) when the
   repository has a `GEMINI_API_KEY` secret: *keep* is published, *drop* is recorded in `never-block.txt` with the
   reason, *unsure* goes to `overrides/pending.txt`. Without a key, new domains wait in `pending.txt`. **Nothing
   new is published unreviewed.** AI companion candidates get their own question: is this an AI companion, AI
   girlfriend/boyfriend or AI character / role-play chat service a child could use, or something else (a general
   assistant, a developer tool, a business that just has "ai" or "character" in its name)?
5. Commit `gaps.txt` / `gaps.json`; an open issue "Domains waiting for review" lists `pending.txt`.

### Deciding pending domains

Move each line of `overrides/pending.txt` to `always-block.txt` (block it) or `never-block.txt` (don't). The next
run (it also runs on every push to `overrides/`) applies it.

### Optional AI review setup

1. Create a Gemini API key at [aistudio.google.com](https://aistudio.google.com/apikey) (free tier).
2. Repository **Settings > Secrets and variables > Actions > New repository secret**: `GEMINI_API_KEY`.
3. Optional variable `GEMINI_MODEL` (default `gemini-flash-lite-latest`).

Only public domain names are sent to the model.

## Run locally

```
python scripts/build.py
```

Standard library only (Python 3.10+). Set `GEMINI_API_KEY` to include the AI review.

## Licence and attribution

GPL-3.0 (see `LICENSE`), because the list is derived from the
[HaGeZi DNS blocklists](https://github.com/hagezi/dns-blocklists) (GPL-3.0). Popularity data:
[Tranco](https://tranco-list.eu), "Tranco: A Research-Oriented Top Sites Ranking Hardened Against Manipulation"
(Le Pochat et al., NDSS 2019).
