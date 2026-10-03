"""Demo presets: real, already-resolved public cases.

VERIFY BEFORE STAGE: the 'truth' text paraphrases each brand's public
statement as reported in the press. Read the original statements and adjust
wording before presenting. Contagion is not affiliated with these brands.
"""

PRESETS = [
    {
        "key": "wendys-surge-pricing",
        "label": "Wendy's 'surge pricing' (Feb 2024)",
        "brand": "Wendy's",
        "claim": "Wendy's is going to raise prices during busy hours with Uber-style surge pricing.",
        "truth": (
            "Wendy's said it will not implement surge pricing and has no plans to raise prices when customers "
            "visit most. It said digital menu boards could be used to offer discounts and value deals during slower times."
        ),
        "truth_url": "",
        "keywords": ["wendys surge pricing", "wendy's dynamic pricing"],
        "since": "2024-02-20",
        "until": "2024-04-30",
        "sources": [
            "https://www.npr.org/2024/02/28/1234412431/wendys-dynamic-surge-pricing",
            "https://www.axios.com/2024/02/28/wendys-pricing-plans-digital-boards",
        ],
    },
    {
        "key": "stanley-lead",
        "label": "Stanley tumblers 'leak lead' (Jan 2024)",
        "brand": "Stanley",
        "claim": "Stanley tumblers leak lead into your drink.",
        "truth": (
            "Stanley said lead is used in the sealing material at the base of its vacuum-insulated products, where it is "
            "covered by a durable stainless steel layer and does not come into contact with the contents of the cup."
        ),
        "truth_url": "",
        "keywords": ["stanley cup lead", "stanley tumbler lead test"],
        "since": "2024-01-10",
        "until": "2024-03-31",
        "sources": [
            "https://politifact.com/factchecks/2024/feb/01/facebook-posts/do-stanley-cups-pose-a-lead-danger-heres-what-to-k/",
            "https://www.cnn.com/2024/01/26/health/stanley-cups-lead-wellness",
        ],
    },
]
