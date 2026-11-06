import json

import pytest

from backend import main

FEED = """<rss><channel><title>The show</title>
<item><title><![CDATA[Ep 2: Pricing &amp; growth]]></title>
<content:encoded><![CDATA[<p>We talk pricing.</p><p>Links:<br/><a href="x">site</a></p>]]></content:encoded></item>
<item><title>Ep 1</title><description>Just a description</description></item>
</channel></rss>"""


def test_parse_feed_gives_title_and_plain_text_notes():
    assert main.parse_feed(FEED) == [("Ep 2: Pricing & growth", "We talk pricing.\nLinks:\nsite"),
                                     ("Ep 1", "Just a description")]


def test_parse_feed_keeps_the_latest_n():
    assert len(main.parse_feed(FEED, n=1)) == 1


@pytest.mark.parametrize("url", ["file:///etc/passwd", "C:/Users/me/.env", "ftp://example.com/feed"])
def test_feed_items_only_fetches_web_urls(url):
    with pytest.raises(ValueError):
        main.feed_items(url)


def test_past_episodes_adds_sessions_recorded_here(tmp_path, monkeypatch):
    memory = tmp_path / "memory.json"
    memory.write_text(json.dumps([{"title": "Our last talk", "chapters": ["Intro", "Hiring"]}]), "utf-8")
    monkeypatch.setattr(main, "MEMORY", memory)
    assert main.past_episodes([("Ep 1", "notes " * 100)]) == ["Ep 1 - " + ("notes " * 100)[:200],
                                                             "Our last talk - Intro, Hiring"]
