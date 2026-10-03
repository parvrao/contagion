"""Judgment layer: claim veracity, amplification risk, channel order, don'ts."""
from app.watch import judgment
from app.watch.models import Narrative, Playbook, Watch, WatchAction, WatchInput
from app.watch.playbook import finalize


def nar(**kw):
    base = dict(title="Surge pricing rumor", claim="Acme will add Uber style surge pricing at lunch", threat_type="pricing",
                severity=2, platforms=["Reddit"], news_outlets=[], engagement=400, score=60)
    base.update(kw)
    return Narrative(**base)


def test_small_rumor_big_brand_stays_quiet_with_triggers():
    amp = judgment.amplification(nar(), brand_audience=2_000_000)
    assert amp["verdict"] == "stay_quiet" and amp["ratio"] == 5000.0
    assert amp["exposure_avoided"] == 2_000_000 - 400
    assert any("news outlet" in t for t in amp["triggers"]) and any("3 or more platforms" in t for t in amp["triggers"])


def test_outgrown_rumor_allows_public_reply():
    amp = judgment.amplification(nar(platforms=["Reddit", "X", "News"], news_outlets=["Daily"]), brand_audience=2_000_000)
    assert amp["verdict"] == "public_ok" and amp["triggers"] == [] and amp["exposure_avoided"] == 0
    assert judgment.amplification(nar(engagement=300_000), brand_audience=2_000_000)["verdict"] == "public_ok"


def test_safety_issue_is_never_silenced():
    amp = judgment.amplification(nar(threat_type="safety", severity=3), brand_audience=2_000_000, veracity="true_unflattering")
    assert amp["verdict"] == "act"


def test_unknown_audience_still_defaults_quiet():
    amp = judgment.amplification(nar(), brand_audience=0)
    assert amp["verdict"] == "stay_quiet" and amp["ratio"] is None and amp["exposure_avoided"] == 0


def test_denying_a_misframed_claim_is_flagged():
    f = judgment.judgment_flags("holding_statement", "That claim is false and baseless.", "x", "misframed")
    assert any("partly true" in x for x in f)
    assert judgment.judgment_flags("holding_statement", "That claim is false.", "x", "false") == []


def test_repeating_rumor_wording_is_flagged():
    claim = "Acme will add Uber style surge pricing at lunch"
    assert judgment.repeats_wording("No, Acme will add Uber style surge pricing is wrong", claim)
    assert not judgment.repeats_wording("Our menu prices don't change by time of day.", claim)


def test_finalize_orders_channels_holds_public_and_fixes_donts():
    w = Watch(input=WatchInput(brand="Acme", brand_audience=1_000_000))
    n = nar()
    pb = Playbook(response_level="respond", veracity="misframed", do_not=["Don't argue in replies.", "Don't name the poster."])
    acts = [WatchAction(narrative_id=n.id, kind=k, title=k, draft=d) for k, d in [
        ("social_reply", "This is false."), ("correction_request", "Please update."), ("internal_brief", "brief"),
        ("faq_update", "Q: Do prices change at lunch? A: No."), ("community_note", "Context: ...")]]
    pb, out = finalize(w, n, pb, acts, {"url": "https://reddit.com/r/x/1"}, 0)
    assert [a.kind for a in out] == ["internal_brief", "faq_update", "correction_request", "community_note", "social_reply"]
    reply = out[-1]
    assert reply.timing.startswith("Hold") and any("partly true" in f for f in reply.flags)
    assert pb.response_level == "prepare" and pb.amplification["verdict"] == "stay_quiet"
    assert pb.do_not[:4] == judgment.FIXED_DONTS and "Don't name the poster." in pb.do_not
    assert pb.amplification["triggers"]


def test_scct_maps_veracity_to_strategy():
    assert judgment.scct("rumor", "false", 2)["strategy"] == "deny"
    assert judgment.scct("pricing", "misframed", 2)["strategy"] == "diminish"
    assert judgment.scct("legal", "true_unflattering", 2)["cluster"] == "preventable"
    assert judgment.scct("safety", "true_unflattering", 1)["strategy"] == "rebuild"
    assert judgment.scct("general", "opinion", 1)["strategy"] == "listen"
    assert judgment.scct("rumor", "unclear", 2)["strategy"] == "confirm_first"


def test_stakeholder_grid():
    rows = judgment.stakeholder_map("safety", 3, 1, "escalate")
    close = {r["stakeholder"] for r in rows if r["quadrant"] == "Manage closely"}
    assert {"Regulators", "Press and journalists", "Investors and board", "Retail and distribution partners"} <= close
    low = judgment.stakeholder_map("pricing", 1, 0, "monitor")
    assert {r["stakeholder"] for r in low if r["quadrant"] == "Manage closely"} == set()


def test_raci_legal_consulted_on_public():
    r = judgment.raci("holding_statement", "PR lead", "respond", "product_issue")
    assert r["R"] == "PR lead" and r["A"] == "Head of Communications" and "Legal" in r["C"] and "Exec sponsor" in r["I"]
    assert "Legal" not in judgment.raci("internal_brief", "Comms", "monitor", "rumor")["C"]
