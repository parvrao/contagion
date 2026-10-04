"""Builds a clearly labeled SYNTHETIC Counter replay (fictional brands, invented posts)."""
import asyncio, os, sys, tempfile, shutil
from datetime import datetime, timedelta, timezone
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["CONTAGION_DATA_DIR"] = tempfile.mkdtemp()
os.environ["CONTAGION_CACHE"] = "0"
sys.path.insert(0, REPO); os.chdir(REPO)
from app import llm, store
from app.counter import engine
from app.counter.models import CounterCase, CounterInput
from app.models import Item, LogLine
from app.sources.base import Source

NOW = datetime.now(timezone.utc)
CSV = open("tests/fixtures/kelvra_inventory_demo.csv").read()
U = "https://example.com/synthetic/"
# (platform, days_ago, engagement, title, label)  label: (friction, first_hand, rumor) or None = unrelated
POSTS = [
 ("Reddit", 1, 412, "My Glacio Arctic 40 leaked all over my car seat again, the lid just does not seal", ("quality defect", True, False)),
 ("TikTok", 1, 2300, "Tipped my Arctic 40 over once and my whole bag was soaked. Never again.", ("quality defect", True, False)),
 ("YouTube", 2, 980, "Glacio Arctic 40 leak test: it leaks the moment it tips over", ("quality defect", True, False)),
 ("Bluesky", 2, 64, "third time my Arctic 40 leaked in my backpack. laptop survived, my patience did not", ("quality defect", True, False)),
 ("Reddit", 3, 188, "Arctic 40 lid leaks from the straw hole when it is on its side, anyone else?", ("quality defect", True, False)),
 ("TikTok", 4, 1500, "POV: you trusted the Glacio lid in your gym bag", ("quality defect", True, False)),
 ("Reddit", 12, 90, "Arctic 40 dripped on my desk, had to stop carrying it in my bag", ("quality defect", True, False)),
 ("YouTube", 20, 300, "6 month review of the Glacio Arctic 40: love the size, hate that it leaks", ("quality defect", True, False)),
 ("Bluesky", 27, 22, "my Arctic 40 leaked again lol", ("quality defect", True, False)),
 ("Reddit", 2, 150, "Dropped my Arctic 40 once and it has a huge dent now, paint chipped off too", ("durability", True, False)),
 ("YouTube", 3, 420, "Glacio Arctic 40 after 3 months: dents and chipped paint everywhere", ("durability", True, False)),
 ("Reddit", 5, 75, "My kid's Arctic 40 got dented the first week of school", ("durability", True, False)),
 ("TikTok", 6, 640, "the dent on my Arctic 40 after one fall off the counter", ("durability", True, False)),
 ("Bluesky", 1, 310, "people are saying Glacio cups have lead in the paint?? is that true", ("safety", False, True)),
 ("TikTok", 2, 5100, "heard the Arctic 40 is poisoning people, someone tested it", ("safety", False, True)),
 ("Bluesky", 3, 120, "someone said Glacio tumblers leach metal into your water, sharing just in case", ("safety", False, True)),
 ("Reddit", 4, 40, "Saw a post that Glacio has lead, any source on this?", ("safety", False, True)),
 ("Reddit", 9, 55, "Paid way too much for my Arctic 40 honestly", ("price/value", True, False)),
 ("Reddit", 16, 30, "Arctic 40 is overpriced for what it is", ("price/value", True, False)),
 ("YouTube", 2, 800, "Glacio Arctic 40 vs five other tumblers, full comparison", None),
 ("Bluesky", 1, 15, "new Glacio colorway just dropped and it is cute", None),
 ("Reddit", 8, 33, "Best tumbler for hiking? Considering the Arctic 40", None),
]
LABEL = {p[3]: p[4] for p in POSTS}

class Synthetic(Source):
    name = platform = "Synthetic posts"
    def enabled(self): return True, ""
    async def search(self, plan):
        return [Item(platform=p, url=f"{U}{i}", title=t, text=t, engagement=e,
                     published_at=(NOW - timedelta(days=d, hours=i % 7)).isoformat()) for i, (p, d, e, t, _) in enumerate(POSTS)]

async def cj(system, user, max_tokens=0):
    if "plan social listening" in system:
        return {"queries": ["Glacio Arctic 40 leaking", "Arctic 40 lid leaks", "Arctic 40 dent", "Glacio tumbler problem"]}
    if "label public posts" in system:
        out = []
        for line in user.split("\n"):
            if not line.startswith("["): continue
            i = int(line[1:line.index("]")]); title = line.split(") ", 1)[1].split(" :: ")[0]
            lab = LABEL.get(title)
            out.append({"i": i, "complaint": bool(lab), "friction": lab[0] if lab else "other",
                        "first_hand": bool(lab and lab[1]), "rumor": bool(lab and lab[2]), "quote": title if lab else ""})
        return out
    if "match a competitor" in system:
        ids = {}
        for line in user.split("\n"):
            if line.startswith("[") and ": " in line:
                cid, rest = line[1:].split("] ", 1); ids[rest.split(":")[0]] = cid
        out = []
        if "quality defect" in ids:
            out.append({"cluster_id": ids["quality defect"], "sku": "KV-SEAL-40", "fit": 0.93,
                        "evidence": "fully leakproof locking lid", "angle": "Tip it over. Nothing spills."})
        if "durability" in ids:
            out.append({"cluster_id": ids["durability"], "sku": "KV-KIDS-14", "fit": 0.71,
                        "evidence": "dent-resistant powder coat", "angle": "Built for drops off the counter."})
        return out
    if "paid social ad drafts" in system:
        if "KV-SEAL-40" in user:
            return {"hooks": ["Soaked bag again?", "Tip it over. Nothing spills.", "Your laptop called. It wants a better lid."],
                    "headlines": ["The lid that actually locks", "Tip it. Toss it. Still dry.", "Leakproof means leakproof"],
                    "body": "Fully leakproof locking lid. Toss it in a bag without spilling. Cold for 24 hours.",
                    "cta": "Shop Now", "audience_ideas": ["gym-goers", "daily commuters", "students with laptop bags"]}
        return {"hooks": ["One drop shouldn't wreck it.", "Kid-proof, not just kid-sized", "Dents happen. Not here."],
                "headlines": ["Dent-resistant by design", "Survives the counter dive", "Tougher than Glacio"],
                "body": "Dent-resistant powder coat, leakproof lid, dishwasher safe. Unlike Glacio.",
                "cta": "Shop Now", "audience_ideas": ["parents of school-age kids", "back-to-school shoppers", "youth sports families"]}
    return {}

async def main():
    llm.available = lambda: True
    llm.complete_json = cj
    engine.all_sources = lambda: [Synthetic()]
    ci = CounterInput(our_brand="Kelvra", competitor="Glacio", competitor_product="Arctic 40",
                      category="insulated tumblers", inventory_source="csv", csv_text=CSV,
                      landing_url="https://example.com/kelvra/sealtight-40", holding_cost_pct_year=20,
                      since=(NOW - timedelta(days=35)).strftime("%Y-%m-%d"))
    case = CounterCase(input=ci); store.save(case)
    await engine.run_counter(case)
    case.sources_status = {"Inventory CSV": case.sources_status.get("Inventory CSV", "ok"), "Synthetic posts": f"SYNTHETIC ({len(POSTS)} invented posts)"}
    case.log.insert(0, LogLine(stage="replay", level="warn", message="SYNTHETIC DEMO DATA: Kelvra and Glacio are fictional brands; every post, number and URL is invented to show how Counter works. Not real customers."))
    out = os.path.join(REPO, "data", "replays", "counter-synthetic-kelvra-vs-glacio.json")
    open(out, "w").write(case.model_dump_json(indent=1))
    print(case.status, case.summary, [(c.friction, c.reality, c.mentions, c.first_hand, c.spike, c.spiking) for c in case.clusters])
    for a in case.actions: print(a.title, a.guardrail_flags)
    print(out)
asyncio.run(main())
