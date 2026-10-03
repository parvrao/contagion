from .bluesky import BlueskySource
from .gnews import GoogleNewsSource
from .hackernews import HackerNewsSource
from .reddit import RedditSource
from .websearch import WebSearchSource
from .youtube import YouTubeSource


def all_sources():
    return [
        RedditSource(),
        HackerNewsSource(),
        GoogleNewsSource(),
        BlueskySource(),
        YouTubeSource(),
        WebSearchSource(),
    ]
