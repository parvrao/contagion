# Contagion

**Rumor trace + AI contamination audit, with a human approval gate.**

A rumor about a brand used to fade. Now it gets cited by AI answer engines and repeated as fact long after the news cycle ends. Contagion finds every public instance of a claim, maps how it moved across platforms, asks the AI engines what they say about it, and drafts the response. A person approves every action. Nothing is sent automatically.

## The four stages

| Stage | What happens |
|---|---|
| 01 Discovery | Claude plans search phrasings, then fans out in parallel: Reddit, Hacker News, Google News, Bluesky, YouTube (optional), plus an open-web sweep via Claude web search for platforms with no open API (TikTok, X, Instagram, Facebook, Threads, forums, blogs). |
| 02 Analysis | Every item is labeled *spreading / correcting / neutral / unrelated*. A skeptic reviewer re-checks every "spreading" label. Timeline, platform hops, link edges between pages, earliest public instance, correction lag. |
| 03 Cross-referencing | The same neutral questions go to every configured AI engine (Claude live web, Claude model memory, ChatGPT, Perplexity, Gemini). Answers are judged (repeats / corrects / unaware), "repeats" must survive a second reviewer, and cited URLs are matched against the trace. Optional: Profound citation counts for the domains carrying the rumor. |
| 04 Synthesis | Escalation grade (1 Contained, 2 Spreading, 3 Mainstream, 4 Contaminated), heuristic risk score, and drafts: site fix with FAQPage JSON-LD, fact sheet, correction requests (pages AI engines cite first), platform review flags. All drafts start **pending**. |

Then **Re-check** re-asks the AI engines later and records the grade history.

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
