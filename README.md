# Contagion

**Rumor trace + AI contamination audit, with a human approval gate.**

A rumor about a brand used to fade. Now it gets cited by AI answer engines and repeated as fact long after the news cycle ends. Contagion finds every public instance of a claim, maps how it moved across platforms, asks the AI engines what they say about it, and drafts the response. A person approves every action. Nothing is sent automatically.

## Hackathon result

Built for the **Profound Marketing Engineering Hackathon**, where the challenge was to solve a marketing problem at a scale traditional processes can't handle. **Contagion advanced to the semifinals**, where I presented the working, deployed prototype, its technical architecture and the business case to the judges.

## The problem

Brands already monitor what people say about them across news, social, forums and review sites. That is no longer the whole picture. AI assistants, search copilots and AI answers embedded in products are now often a person's first source of information about a company, and those systems learn from what is published. The question Contagion answers: **how do you manage not only what people say about you, but what AI systems say about you?**

## What I built

A working, deployed prototype with three connected capabilities:

1. **Core monitoring (Watch).** Continuously gathers narratives from news, social platforms, forums and AI ecosystems. AI classifies each claim, groups related stories into narratives, scores risk, and recommends one of four moves: respond, monitor, correct, or avoid amplifying the issue.
2. **Deep Trace.** For higher-risk narratives, traces the story from origin to propagation: where it started, how it moved across platforms, which sources amplified or corrected it, and whether AI systems began repeating or citing the claim. An alert becomes an investigation of the narrative path and of the sources shaping public and AI-generated answers.
3. **Counter.** Extends the platform from reputation defense to market opportunity. Finds verified competitor pain points, matches them to available inventory, estimates the opportunity with unit economics, and drafts campaigns with guardrails and human approval before anything runs.

A decision-support layer sits across all three. It evaluates each situation the way a brand or communications manager would (risk, stakeholder impact, response options, reputational implications) using frameworks such as SCCT, a Mendelow power/interest grid and RACI. It recommends; it never decides. Every action needs human review and approval.

**AI visibility** was the deliberate extension of the original problem. Using Profound APIs, Contagion checks whether a narrative has started appearing in AI-generated answers, which sources influence those answers, and whether corrective actions change the outcome over time.

## How it works, end to end

1. A narrative enters from an external source.
2. Contagion analyzes the content and classifies the claim (false, misframed, true but unflattering, opinion, unclear).
3. It groups the claim with related coverage and assesses potential impact.
4. It checks whether the same narrative appears in AI-generated answers, using Profound's AI visibility data and the AI engine probes.
5. Higher-risk cases escalate to **Deep Trace**. Verified competitor weaknesses route to **Counter** for opportunity analysis and campaign drafting.
6. Recommended actions and drafts wait for human approval. **Re-check** later measures whether the AI answers changed.

Governing principle: **the LLM writes, the code decides, humans approve.** Models handle analysis, classification, clustering, investigation and drafting. Business rules, workflow control and governance live in deterministic code, outside the model.

## How I presented it

For the semifinal I did not show a dashboard alone. I:

- Framed the project around a practical marketing and communications scenario.
- Ran the deployed product live.
- Walked the judges through the decision flow: detection, investigation, recommended action.
- Tied the architecture to the governance principle above, so the technical choices and the business case told one story.

## Tech stack

Python, FastAPI, JavaScript (vanilla, no build step), Claude, Gemini, Profound APIs, Pytest, deployed on Render. Open-source tooling and cloud services throughout.

## Watch mode (live brand threat monitor)

Type a brand. Contagion pulls the last 7 days, then keeps polling (News, Hacker News, Bluesky every 90 s; YouTube and a Claude open-web sweep for TikTok, X, Reddit and forums about every 15 min). Every new mention is triaged (sentiment, threat type, severity, stance) and filed under a **narrative**. Each narrative gets a 0 to 100 threat score (severity, volume, velocity vs baseline, platform reach, news pickup, negativity, unanswered share) and a status (emerging, escalating, active, fading).

Alerts fire when a serious narrative appears, crosses score 45 or 70, jumps platforms, reaches a news outlet or starts escalating. They show in the dashboard, as desktop notifications, and in Slack (`SLACK_WEBHOOK_URL`, internal only).

Narratives scoring 45+ get a **response plan**: plain-English assessment, a response level (monitor, prepare, respond, escalate), what not to do, and owner-assigned actions with drafts (internal brief, holding statement, support macro, FAQ update, correction request). Every action is pending until a person approves, edits or rejects it. **Deep trace** runs the full four-stage pipeline below on any narrative.

## The four stages (Deep trace)

| Stage | What happens |
|---|---|
| 01 Discovery | Claude plans search phrasings, then fans out in parallel: Reddit, Hacker News, Google News, Bluesky, YouTube (optional), plus an open-web sweep via Claude web search for platforms with no open API (TikTok, X, Instagram, Facebook, Threads, forums, blogs). |
| 02 Analysis | Every item is labeled *spreading / correcting / neutral / unrelated*. A skeptic reviewer re-checks every "spreading" label. Timeline, platform hops, link edges between pages, earliest public instance, correction lag. |
| 03 Cross-referencing | The same neutral questions go to every configured AI engine (Claude live web, Claude model memory, ChatGPT, Perplexity, Gemini). Answers are judged (repeats / corrects / unaware), "repeats" must survive a second reviewer, and cited URLs are matched against the trace. Optional: Profound citation counts for the domains carrying the rumor. |
| 04 Synthesis | Escalation grade (1 Contained, 2 Spreading, 3 Mainstream, 4 Contaminated), heuristic risk score, and drafts: site fix with FAQPage JSON-LD, fact sheet, correction requests (pages AI engines cite first), platform review flags. All drafts start **pending**. |

Then **Re-check** re-asks the AI engines later and records the grade history.

## Counter mode: competitor gap x surplus inventory

| Stage | What happens |
|---|---|
| 01 Inventory | Shopify (Dev Dashboard app: `read_products`, `read_inventory`, `read_orders`) or CSV. Pure arithmetic: days of supply, margin, max CAC = unit profit x CAC share, surplus units over the target. |
| 02 Listening | Same source connectors, pointed at the competitor's product. Each post: complaint? friction type, first-hand or second-hand, rumor? verbatim quote. Grouped into friction clusters with 7-day trend. |
| 03 Reality check | A cluster is **verified** only with 3+ first-hand reports on 2+ platforms. Rumor-led clusters are **disputed** and never used: advertising against a false claim about a competitor is a false-advertising risk. |
| 04 Match & draft | Verified pain point x surplus SKU, only when the SKU's own description answers it (evidence must be a verbatim substring). Ad package: hooks, headlines, body, CTA, audience ideas, suggested daily budget (heuristic, capped). |

Also in Counter:
- **Dollar impact:** cash tied up in surplus, monthly holding cost (your stated %/yr assumption), revenue and gross profit after max CAC for SKUs that got an ad draft. Arithmetic only.
- **Stockout guard:** SKUs under N days of supply are never advertised.
- **Spike score:** last 7 days vs. a 4-week baseline (days 8 to 35), by mentions and by engagement; "new this week" when there's no baseline. YouTube comment-to-view ratio when `YOUTUBE_API_KEY` is set. Historical runs measure from the end of the date window.
- **Verified competitor facts:** fact + exact value + source URL + check date (max 14 days old). Only those values may appear in comparison copy; they're listed as substantiation in the export.
- **Structured brief:** the exact JSON the drafting model receives is shown on every ad card.

Guardrails: competitor names/products in copy **block approval** until edited; numbers not in the product data are flagged; edited text is re-checked on approval. Approved packages export as a bulk-import CSV with status **PAUSED**. Nothing is published and no money is spent from the tool.

Counter only runs on real inventory: a connected Shopify store or your own CSV export. The fictional "Northpace" file lives in `tests/fixtures/` and is used by tests only.

## Response judgment (Watch plans)

Contagion tells you when to stay quiet, and when you do act, it corrects the record where people check later instead of arguing in threads.

1. **How true is it?** Every plan first classifies the claim: false, misframed (real fact, wrong conclusion), true but unflattering, opinion, or unclear. It records the true part and the missing context. Drafts that deny a misframed or true claim are flagged.
2. **Would replying amplify it?** Rumor reach (engagement on the posts found, a lower bound) vs. the brand's audience. Staying quiet is the default until the rumor reaches the news on 3+ platforms or a tenth of the brand's audience; the plan lists the triggers that would change that and how much extra exposure silence avoided. Safety, legal and outage issues are never silenced.
3. **Channel order:** (1) the page AI engines and search will cite, checked with Profound and the AI engine check, (2) the original high-reach source (Deep trace's earliest post when available), (3) platform tools such as Community Notes and journalist corrections, (4) a public reply, held under a stay-quiet verdict.
4. **Fixed don'ts in every plan:** don't repeat the rumor's wording (checked automatically), don't argue in replies, don't deny the true parts, don't respond before the rumor has a big enough audience.
5. **Frameworks, each driving a decision:** SCCT (Coombs) picks the response strategy from the claim's veracity: deny only a false rumor, diminish a misframed one, rebuild for a true failure, listen to opinion, confirm facts first when unclear. A Mendelow power/interest grid says who to brief first. Every action carries RACI (Legal is always Consulted on anything public).

Counter ads also carry a positioning statement (Moore's template, filled only from verified inputs) and unit economics: gross profit per unit, suggested spend, units to cover it, and days to clear the surplus at today's pace with the holding cost of waiting.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # add ANTHROPIC_API_KEY at minimum
uvicorn app.main:app --reload
# open http://localhost:8000
pytest -q                   # tests use fakes, no keys or network needed
```

## Deploy (Render or anything that runs a Python web process)

`render.yaml` and `Procfile` are included. **Set `CONTAGION_ACCESS_TOKEN`** before exposing it, or anyone with the URL can spend your API credits. Render's disk is ephemeral, so attach a disk and point `CONTAGION_DATA_DIR` at it if cases need to survive restarts.

## Design rules

- **Human gate:** only approved actions are exported (CSV) or opened in email. The tool never posts, emails or reports anything itself.
- **No doxxing:** the tool collects public handles and outlets only, never locations or networks of people. Platform flags ask for review of the *post*, not action against the person.
- **Fail closed on accusations:** if the skeptic pass can't run, nothing gets flagged to a platform. Unconfirmed "repeats" verdicts are shown but don't raise the grade.
- **Guardrail:** drafts are checked for numbers or links that aren't in the brand's own statement.
- **Fail soft on sources:** a dead source or engine is reported in the run's Log tab; the run continues.
- **Honest dates:** dates reported by the search model are marked and excluded from "earliest instance".

## Demo insurance

After a good live run, open **Log > Save as replay**. Replays load from the home page with a clear "recorded run" banner if the venue network fails.

## Layout

```
app/
  main.py         API, SSE progress, approval endpoints, exports, static UI
  pipeline.py     the four stages + re-check
  sources/        one connector per platform (same contract)
  probes/         AI engines + Profound
  analyze.py      LLM judgments with deterministic fallbacks
  spread.py       timeline, hops, edges, cross-reference, grade (pure functions)
  respond.py      drafts + guardrail
  http.py         shared client: retries, backoff, Retry-After, per-host limits
web/              vanilla HTML/CSS/JS, no build step
tests/            end-to-end with fakes
```

## Known limits (say these out loud)

- Coverage is public, indexed content only. Closed platforms are reached through search results, not APIs.
- "Earliest instance found" is not proof of origin.
- The risk score weights are a heuristic, not a calibrated model.
- Demo presets are public, resolved 2024 cases. Contagion is not affiliated with those brands; verify the brand statements before quoting them.
