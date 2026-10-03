"""Response kit. Every action is a DRAFT with status=pending. Nothing here
sends anything: a person approves, edits or rejects each one in the UI, and
only approved actions are exported."""
from __future__ import annotations

import json
import re

from . import llm
from .models import Action, CaseInput, Item

SOCIAL = {"Reddit", "Bluesky", "YouTube", "X", "TikTok", "Instagram", "Facebook", "Threads", "Hacker News", "LinkedIn"}
REPORT_HOWTO = {
    "Reddit": "Use 'Report' on the post > 'Misinformation' (or the subreddit's rule), or message the subreddit moderators.",
    "X": "Use the post menu > 'Report post' > misleading info; or add context via Community Notes.",
    "TikTok": "Long-press the video > 'Report' > 'Misinformation'.",
    "Instagram": "Post menu > 'Report' > 'False information'.",
    "Facebook": "Post menu > 'Report post' > 'False information'.",
    "Threads": "Post menu > 'Report' > 'False information'.",
    "YouTube": "Video menu > 'Report' > 'Misinformation'.",
    "Bluesky": "Post menu > 'Report post' > 'Misleading'.",
    "Hacker News": "Email hn@ycombinator.com with the item link; HN moderates by hand.",
    "LinkedIn": "Post menu > 'Report post' > 'Misinformation'.",
}


def numbers_in(text: str) -> set[str]:
    """Whole numbers present in text (so '1' is not 'found' inside '130')."""
    nums = set()
    for n in re.findall(r"\d+(?:[.,]\d+)?", text):
        nums.add(n)
        try:
            f = float(n.replace(",", ""))
            nums.add(f"{f:g}")
        except ValueError:
            pass
    return nums


def guardrail(text: str, ci: CaseInput, allowed_urls: list[str]) -> list[str]:
    """Flag numbers and links in a draft that don't come from the brand's own inputs."""
    source = f"{ci.claim} {ci.truth} {ci.truth_url} {ci.brand}"
    flags = []
    no_urls = re.sub(r"https?://\S+", " ", text)  # digits inside links are not claims
    allowed_nums = numbers_in(source)
    for num in set(re.findall(r"\d+(?:[.,]\d+)?%?", no_urls)):
        if num.rstrip("%") not in allowed_nums:
            flags.append(f"number '{num}' is not in the brand's statement: verify or remove")
    for url in set(re.findall(r"https?://\S+", text)):
        url = url.rstrip(").,")
        if url not in allowed_urls and url not in source:
            flags.append(f"link {url} is not from the inputs: verify")
    return flags


def faq_jsonld(ci: CaseInput, question: str) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{
            "@type": "Question",
            "name": question,
            "acceptedAnswer": {"@type": "Answer", "text": ci.truth},
        }],
    }
    return json.dumps(data, indent=2)


async def build_actions(ci: CaseInput, items: list[Item], contamination: dict, questions: list[str]) -> list[Action]:
    actions: list[Action] = []
    by_id = {i.id: i for i in items}
    allowed = [i.url for i in items] + ([ci.truth_url] if ci.truth_url else [])

    # 1. Fact sheet (model-written, constrained to the brand's statement).
    sheet = await _fact_sheet(ci)
    actions.append(Action(kind="fact_sheet", title="Public fact sheet / holding statement", draft=sheet,
                          guardrail_flags=guardrail(sheet, ci, allowed)))

    # 2. Site fix: give AI engines something true and structured to cite.
    q = questions[0] if questions else f"Is it true that {ci.claim.rstrip('.?')}?"
    site = (
        f"Publish a short Q&A on {ci.truth_url or 'the brand site (official newsroom or FAQ)'} that answers the question "
        f"people are asking AI engines, in plain words, with a visible date. Add this FAQPage markup:\n\n"
        f"<script type=\"application/ld+json\">\n{faq_jsonld(ci, q)}\n</script>"
    )
    actions.append(Action(kind="site_fix", title="Give AI engines a source of truth to cite", target_url=ci.truth_url,
                          draft=site, guardrail_flags=[]))

    # 3. Correction requests: pages AI engines cite when repeating the rumor first, then top news amplifiers.
    targets: list[tuple[str, str, str]] = []  # (url, title, why)
    for page in contamination.get("pages_feeding_ai", []):
        it = by_id.get(page.get("item_id") or "")
        if it and it.stance == "debunks":
            continue
        why = f"cited by {', '.join(page['engines'])} in answers that repeat the rumor"
        targets.append((it.url if it else page["url"], (it.title if it else page.get("title")) or page["url"], why))
    news_amps = sorted([i for i in items if i.stance == "amplifies" and i.platform not in SOCIAL],
                       key=lambda i: i.published_at or "9999")
    for it in news_amps:
        targets.append((it.url, it.title or it.url, f"{it.author or it.domain} presents the rumor as true ({it.stance_reason})"))
    seen = set()
    for url, title, why in targets:
        if url in seen or len(seen) >= 8:
            continue
        seen.add(url)
        draft = (
            f"Subject: Correction request: {title[:90]}\n\n"
            f"Hello,\n\nWe're reaching out from {ci.brand} about this page:\n{url}\n\n"
            f"It states or implies that {ci.claim.rstrip('.')}. That is not accurate. {ci.truth}\n"
            + (f"\nOur official statement: {ci.truth_url}\n" if ci.truth_url else "")
            + "\nWould you consider updating the page or adding a correction note? We're happy to answer questions.\n\nThank you,\n[Name], "
            + ci.brand
        )
        actions.append(Action(kind="correction_request", title=title[:140], target_url=url, draft=draft,
                              rationale=why, guardrail_flags=guardrail(draft, ci, allowed + [url])))

    # 4. Platform review flags for the highest-reach social posts spreading it.
    social_amps = sorted([i for i in items if i.stance == "amplifies" and i.confirmed and i.platform in SOCIAL],
                         key=lambda i: -i.engagement)[:8]
    for it in social_amps:
        draft = (
            f"Post: {it.url}\nPlatform: {it.platform}\nAccount (public): {it.author or 'unknown'}\n"
            f"Issue: the post presents a false claim about {ci.brand} as fact: \"{ci.claim.rstrip('.')}\".\n"
            f"Correction: {ci.truth}\n" + (f"Source: {ci.truth_url}\n" if ci.truth_url else "")
            + f"\nHow to submit: {REPORT_HOWTO.get(it.platform, 'Use the platform report flow for misleading content.')}\n"
            "Request: review under the platform's misleading-information policy. We are not asking for any action against the person beyond the post."
        )
        actions.append(Action(kind="platform_flag", title=f"{it.platform}: {(it.title or it.text)[:100]}", platform=it.platform,
                              target_url=it.url, draft=draft, rationale=it.stance_reason,
                              guardrail_flags=guardrail(draft, ci, allowed)))
    return actions


async def _fact_sheet(ci: CaseInput) -> str:
    fallback = (
        f"FACT CHECK: {ci.brand}\n\nWhat people are saying: {ci.claim}\n\nWhat's true: {ci.truth}\n"
        + (f"\nOfficial source: {ci.truth_url}" if ci.truth_url else "")
    )
    if not llm.available():
        return fallback
    try:
        text, _ = await llm.complete(
            "You write short, plain, confident brand fact sheets. Use ONLY facts in the brand's statement. "
            "No new numbers, dates, names or promises. No marketing fluff. Under 140 words. Plain text, no markdown headers.",
            f"Brand: {ci.brand}\nRumor: {ci.claim}\nBrand's statement (the only allowed facts): {ci.truth}\n"
            f"Official link: {ci.truth_url or 'none'}\n\nWrite: a one-line headline, 'What people are saying', 'What's true', and the link if given.",
            max_tokens=500,
        )
        return text or fallback
    except Exception:
        return fallback
