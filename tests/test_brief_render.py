"""`compile/brief_render.py`: the page is written by code from validated JSON; nothing the model or a mail wrote can become markup."""

import re

import pytest
from html.parser import HTMLParser

from ace_playbook.compile import brief_render as R

PAYLOAD = '<script>alert(1)</script> " javascript:alert(1) <img src=x onerror=alert(1)>'


def item(**kw):
    return {"title": "Reply to the grant question", "sentence": "Asked on Monday whether the draft is final.", "source": "in your inbox",
            "url": None, **kw}


def sample(**kw):
    return {"generated_at": "2026-10-21T08:05:00-07:00", "day_class": "heavy", "headline": "A steady climb until 2, then the day opens up.",
            "acts": [{"range": "9:30 AM – 1 PM", "sentence": "Three meetings back to back."},
                     {"range": "1 – 3:30 PM", "sentence": "One review, then quiet."},
                     {"range": "3:30 PM onward", "sentence": "The rest is yours."}],
            "needs_you": [item(url="https://mail.google.com/mail/u/0/#inbox/abc")], "resolved": [item(title="Venue booked")], **kw}


def injection():
    s = PAYLOAD
    return {"generated_at": s, "day_class": s, "headline": s,
            "acts": [{"range": s, "sentence": s, "load": s} for _ in range(3)],
            "needs_you": [{"title": s, "sentence": s, "source": s, "url": "javascript:alert(1)"},
                          {"title": s, "sentence": s, "source": s, "url": 'https://mail.google.com/a"onmouseover="alert(1)'},
                          {"title": s, "sentence": s, "source": s, "url": "https://linear.app/ok?q=1&r=<b>"}],
            "resolved": [{"title": s, "sentence": s, "source": s, "url": "data:text/html,<script>alert(1)</script>"}]}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]

    def handle_data(self, data):
        self.text.append(data)


def parsed(page):
    p = Tags()
    p.feed(page)
    return p


def test_every_string_field_is_escaped_and_no_markup_survives():
    page = R.render(injection())
    p = parsed(page)
    assert "<script" not in page.lower() and "script" not in p.tags and "img" not in p.tags
    assert set(p.tags) <= {"html", "head", "meta", "title", "style", "body", "div", "p", "h1", "h2", "svg", "polyline", "circle",
                           "section", "ol", "li", "span", "a"}
    assert not [a for a in p.attrs if a[1].startswith("on")]                       # no event handler, even from the quote payload
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;img src=x onerror=alert(1)&gt;" in page and "&quot;" in page
    assert page.count(PAYLOAD) == 0 and PAYLOAD.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;") in page


def test_javascript_and_data_urls_are_dropped_and_only_https_links_are_written():
    p = parsed(R.render(injection()))
    hrefs = [v for tag, k, v in p.attrs if tag == "a" and k == "href"]
    assert hrefs == ['https://mail.google.com/a"onmouseover="alert(1)', "https://linear.app/ok?q=1&r=<b>"]   # unescaped by the parser: one attribute each
    assert all(h.startswith("https://") for h in hrefs)
    assert [v for tag, k, v in p.attrs if tag == "a" and k == "rel"] == ["noopener noreferrer"] * 2
    assert not [a for a in p.attrs if a[1] in ("src", "srcset", "action", "formaction")]
    assert 'href="javascript' not in R.render(injection()) and "data:text" not in "".join(v for _, k, v in p.attrs if k == "href")


def test_the_csp_meta_is_present_with_no_script_no_button_no_external_resource():
    page = R.render(sample())
    assert '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">' in page
    p = parsed(page)
    assert not {"script", "button", "form", "input", "iframe", "link", "img", "object", "embed", "base"} & set(p.tags)
    assert "://" not in re.sub(r'href="https://mail\.google\.com/mail/u/0/#inbox/abc"', "", page)        # the one link is the only URL on the page
    assert "@font-face" not in page and "url(" not in page and "@import" not in page and "base64" not in page


def test_both_themes_define_their_tokens_on_root_and_the_body_has_a_background():
    page = R.render(sample())
    light, dark = page.split("prefers-color-scheme: dark")
    for tok in ("#FCFCFB", "#F9F9F7", "#2E2C27", "#6B6A63", "#B4B3A8", "#E4E3DC"):
        assert tok in light.split("@media")[0]
    for tok in ("#151513", "#1C1C19", "#ECEAE2", "#A9A79D", "#6E6D66", "#2E2E2A"):
        assert tok in dark
    assert ":root{--bg:#FCFCFB" in page and "body{margin:0;background:var(--bg)" in page
    assert "max-width:860px" in page and "@media (max-width: 640px)" in page and "Fraunces,Georgia,serif" in page


def test_the_layout_follows_the_skill():
    page = R.render(sample())
    for want in ("Wednesday · October 21 2026", "<h1>A steady climb until 2, then the day opens up.</h1>", "9:30 AM – 1 PM", "3:30 PM onward",
                 "Needs attention", "Resolved", '<span class="n">01</span>', "Venue booked", "<polyline"):
        assert want in page
    assert page.count('class="act"') == 3 and page.count("<polyline") == 1 and page.count('class="accent"') == 1
    assert page.index("Needs attention") < page.index("Resolved") and page.index("Wednesday") < page.index("<h1>")
    assert '<a href="https://mail.google.com/mail/u/0/#inbox/abc" rel="noopener noreferrer">in your inbox</a>' in page


def test_an_empty_day_renders_the_calm_line_in_place_of_both_lists():
    page = R.render(sample(needs_you=[], resolved=[]))
    assert "Nothing needs you this morning." in page and "Needs attention" not in page and "Resolved" not in page
    only = R.render(sample(resolved=[]))
    assert "Needs attention" in only and "Resolved</h2>" not in only and "Nothing needs you" not in only


def test_the_terrain_is_flat_on_an_open_day_and_climbs_on_a_heavy_one():
    def ys(loads):
        pts = re.search(r'points="([^"]+)"', R.terrain(loads)).group(1).split()
        return [float(p.split(",")[1]) for p in pts]
    flat, hill = ys(["open"] * 3), ys(["heavy", "normal", "open"])
    assert max(flat) - min(flat) < 4 and max(hill) - min(hill) > 60 and min(hill) < min(flat)
    assert R.terrain(["bogus"] * 3) == R.terrain(["normal"] * 3)            # an unknown load is a normal one, never an injection point


def test_a_date_that_does_not_parse_is_left_out():
    assert 'class="date"' not in R.render(sample(generated_at="tomorrow-ish"))


AT = chr(64)          # spelled out so the scrubber does not read the userinfo URLs as addresses
OFF_LIST = ["https://evil.example/c?d=SUBJECTS", "https://evil" + AT + "mail.google.com/", "https://mail.google.com:443/",
            "https://mail.google.com.evil.example/",
            "https://mail.google.com\\" + AT + "evil.example/", "https://MAIL.google.com/", "http://mail.google.com/", "https://example.com/thread/1",
            "https://mail.google.com" + AT + "evil.example/", "https://xmail.google.com/", "https://linear.app.evil.example/"]


def test_a_url_off_the_host_allowlist_renders_no_link_even_if_it_reaches_the_renderer_unvalidated():
    assert R.LINK_HOSTS == ("mail.google.com", "calendar.google.com", "linear.app")
    for url in OFF_LIST:
        raw = sample(needs_you=[item(url=url)], resolved=[])
        assert "<a " not in R.render(raw), url                                           # render() re-checks, it does not trust the validator
        assert R.validate_brief(raw)["needs_you"][0]["url"] is None, url
    for url in ("https://mail.google.com/mail/u/0/#inbox/x", "https://calendar.google.com/calendar/event?eid=1", "https://linear.app/team/issue/ENG-1"):
        assert R.validate_brief(sample(needs_you=[item(url=url)]))["needs_you"][0]["url"] == url


def test_the_csp_meta_comes_right_after_the_charset_and_list_lengths_refuse_instead_of_truncating():
    page = R.render(sample())
    assert page.index("<head>") + 6 == page.index('<meta charset="utf-8">') and page.index('<meta charset="utf-8"><meta http-equiv="Content-Security') > 0
    assert R.validate_brief(sample(needs_you=[item()] * 25))["needs_you"][0]["title"]
    for bad in (sample(needs_you=[item()] * 26), sample(resolved=[item()] * 26), sample(acts=sample()["acts"] * 2)):
        with pytest.raises(R.SchemaError):
            R.validate_brief(bad)
