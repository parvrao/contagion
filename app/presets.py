"""Demo presets: real, already-resolved public cases.

The 'truth' text quotes each brand's public
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
            "Wendy's statement (Feb 28, 2024), as quoted by Axios: \"To clarify, Wendy's will not implement surge pricing.\" "
            "\"We didn't use that phrase, nor do we plan to implement that practice.\""
        ),
        "truth_url": "https://www.axios.com/2024/02/28/wendys-pricing-plans-digital-boards",
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
            "Stanley statement (support page, Jan 2024), as quoted by PolitiFact: \"Rest assured that no lead is present on the surface "
            "of any Stanley product that comes into contact with the consumer nor the contents of the product.\" "
            "PolitiFact rated the claim that lead is inside the cup where the drink is: False."
        ),
        "truth_url": "https://politifact.com/factchecks/2024/feb/01/facebook-posts/do-stanley-cups-pose-a-lead-danger-heres-what-to-k/",
        "keywords": ["stanley cup lead", "stanley tumbler lead test"],
        "since": "2024-01-10",
        "until": "2024-03-31",
        "sources": [
            "https://politifact.com/factchecks/2024/feb/01/facebook-posts/do-stanley-cups-pose-a-lead-danger-heres-what-to-k/",
            "https://www.cnn.com/2024/01/26/health/stanley-cups-lead-wellness",
        ],
    },
]
