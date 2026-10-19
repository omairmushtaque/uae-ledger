"""Write offline test feeds. Companies and outlets are fictional on purpose."""
import json, os, sys
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

def make(outdir):
    os.makedirs(outdir, exist_ok=True)
    now = datetime.now(timezone.utc)
    d = lambda mins: format_datetime(now - timedelta(minutes=mins))

    def rss(items, aggregator=False):
        out = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>']
        for title, url, mins, *src in items:
            s = f'<source url="https://x.example">{src[0]}</source>' if aggregator else ""
            t = f"{title} - {src[0]}" if aggregator else title
            out.append(f"<item><title>{t}</title><link>{url}</link><pubDate>{d(mins)}</pubDate><description>&lt;p&gt;context for {title[:20]}&lt;/p&gt;</description>{s}</item>")
        return "".join(out) + "</channel></rss>"

    u = lambda slug, host: f"https://{host}.example/news/{slug}"
    files = {
        "demo_gazette.xml": rss([
            ("Northgate Bank posts 18% rise in third-quarter profit", u("nb-profit", "gazette"), 50),
            ("Northgate Bank raises dividend to 40 fils a share", u("nb-dividend", "gazette"), 200),
            ("Sandstone Properties launches three towers on Dubai Creek", u("ss-towers", "gazette"), 130),
            ("Falcon Air Cargo orders six freighters for Dubai hub", u("falcon", "gazette"), 300),
            ("Old story about Dubai trade from last week", u("old", "gazette"), 60 * 24 * 5),
        ]),
        "sample_business_daily.xml": rss([
            ("Northgate Bank third-quarter profit jumps 18 percent on lending growth", u("nb-q3", "sbd"), 75),
            ("Sandstone Properties unveils Dubai Creek towers, three buildings planned", u("towers", "sbd"), 110),
            ("UAE central bank keeps base rate unchanged at 4.4 percent", u("rate", "sbd"), 400),
            ("Oasis Fintech raises $40 million to expand in Abu Dhabi", u("oasis", "sbd"), 180),
        ]),
        "test_wire.xml": rss([
            ("Dubai lender Northgate Bank reports 18% higher Q3 profit", u("lender", "wire"), 40),
            ("Central bank of UAE holds base rate at 4.4%", u("hold", "wire"), 380),
            ("Meridian Holdings files for Abu Dhabi stock market listing", u("meridian", "wire"), 240),
            ("Fujairah oil storage capacity expands by two million barrels", u("fujairah", "wire"), 500),
        ]),
        "fixture_agency.xml": rss([
            ("Sandstone Properties to build three towers along Dubai Creek", u("agency-towers", "agency"), 95),
            ("Dubai port container volumes rise in September", u("ports", "agency"), 600),
            ("Sharjah airport freight revenue climbs as cargo demand grows", u("sharjah", "agency"), 700),
            ("Tokyo shares close higher on tech rally", u("tokyo", "agency"), 30),
        ]),
        "aggregator.xml": rss([
            ("Northgate Bank third quarter profit up 18%", "https://agg.example/r/aaa111", 45, "Placeholder Post"),
            ("Falcon Air Cargo orders six freighters in Dubai expansion", "https://agg.example/r/bbb222", 280, "Sample Business Daily"),
            ("Ajman free zone signs tenants for logistics park", "https://agg.example/r/ccc333", 800, "Placeholder Post"),
        ], aggregator=True),
        "dupe_feed.xml": rss([
            ("Northgate Bank posts 18% rise in third-quarter profit", u("nb-profit", "gazette") + "?utm_source=rss", 50),
            ("Dubai free zone trade volume hits record this year", u("freezone", "dupe"), 900),
        ]),
        "broken.xml": "<rss><channel><item><title>oops",
        "empty.xml": '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>',
    }
    for name, content in files.items():
        open(os.path.join(outdir, name), "w").write(content)

    feeds = [
        {"name": "Demo Gazette", "url": "demo_gazette.xml", "type": "rss"},
        {"name": "Sample Business Daily", "url": "sample_business_daily.xml", "type": "rss"},
        {"name": "Test Wire", "url": "test_wire.xml", "type": "rss"},
        {"name": "Fixture News Agency", "url": "fixture_agency.xml", "type": "rss", "filter": True},
        {"name": "Aggregator query", "url": "aggregator.xml", "type": "rss", "outlet_from_source": True},
        {"name": "Dupe Feed", "url": "dupe_feed.xml", "type": "rss"},
        {"name": "Broken Feed", "url": "broken.xml", "type": "rss"},
        {"name": "Empty Feed", "url": "empty.xml", "type": "rss"},
        {"name": "Missing Feed", "url": "does_not_exist.xml", "type": "rss"},
    ]
    json.dump({"outlet_weights": {"demo gazette": 3, "sample business daily": 3, "test wire": 2}, "feeds": feeds},
              open(os.path.join(outdir, "feeds.json"), "w"), indent=1)

if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures")
