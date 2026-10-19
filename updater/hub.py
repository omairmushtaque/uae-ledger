#!/usr/bin/env python3
"""UAE business and finance news hub: collect, group, summarize, check, build.

Standard library only. Run:  python3 updater/hub.py
Needs ANTHROPIC_API_KEY for summaries; without it the page shows headlines only.
What is stored/published: headline, link, outlet name, time, and our own summary.
Feed snippets are used as summarizer input in memory and are never saved or shown.
"""
import argparse, hashlib, html, json, math, os, re, ssl, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

SITE_NAME = "UAE Ledger"
TAGLINE = "UAE business and finance, grouped by story."
UA = "Mozilla/5.0 (compatible; UAELedgerBot/0.1; +news aggregator, links back to sources)"
WINDOW = 48 * 3600          # items older than this are dropped; also max gap inside a story
SIM, MIN_SHARED, MERGE_SIM = 0.50, 3, 0.60
MAX_STORIES = 40
STALE_HOURS = 72            # a feed whose newest item is older than this is called stale
GST = timezone(timedelta(hours=4))

UAE_RE = re.compile(r"\b(uae|emirates|emirati|dubai|abu dhabi|sharjah|ajman|fujairah|ras al khaimah|umm al quwain|adnoc|dfm|adx|difc|adgm|mubadala|adq|adia|dirham|aed|etihad|emaar|aldar|g42|masdar|taqa)\b", re.I)
BIZ_RE = re.compile(r"\b(business|econom|bank|financ|stock|share|market|invest|fund|ipo|profit|revenue|earning|trade|trading|oil|gas|energy|propert|real estate|tower|develop|project|contract|startup|fintech|loan|bond|sukuk|tax|gdp|inflation|rate|deal|acqui|merger|listing|billion|million|compan|firm|ceo|growth|dirham|aed|cargo|airline|logistic|capital|price|sale|retail|export|import|ports?\\b|container|volume|freight|shipping|hotel|tourism)", re.I)
STOP = set("a an the of to in on for and or as at by with from is are be was were will its it this that after over amid into new says say said has have had not but their his her more than up out off about year years uae dubai abu dhabi emirates".split())
BAD_PHRASES = re.compile(r"—|–|testament|pivotal|landscape|underscore|delve|not just|not only|game-chang|marks a ", re.I)


# ---------- small helpers ----------
def toks(s):
    out = []
    for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", s.lower()):
        if w in STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out

def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()

def canon(url):
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_", "ref"))]
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urllib.parse.urlencode(q), ""))

def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.strptime(s, "%Y%m%dT%H%M%SZ") if re.fullmatch(r"\d{8}T\d{6}Z", s) else datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())

def ln(tag):
    return tag.rsplit("}", 1)[-1].lower()


# ---------- 1. collect ----------
def fetch_raw(url, base):
    if not url.startswith("http"):                      # local fixture file
        with open(os.path.join(base, url), "rb") as f:
            return f.read()
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, application/json, */*"})
            with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
                return r.read(3_000_000)
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last

def parse_items(raw, feed):
    items = []
    if feed.get("type") == "gdelt":
        for a in json.loads(raw).get("articles", []):
            items.append(dict(title=a.get("title", ""), url=a.get("url", ""), src=a.get("domain", ""), date=a.get("seendate"), snippet=""))
        return items
    root = ET.fromstring(raw.lstrip())
    for el in root.iter():
        if ln(el.tag) not in ("item", "entry"):
            continue
        d = {}
        for c in el:
            k = ln(c.tag)
            if k == "link" and c.get("href"):
                if c.get("rel", "alternate") == "alternate" and "link" not in d:
                    d["link"] = c.get("href")
            elif k not in d:
                d[k] = (c.text or "").strip()
        items.append(dict(title=d.get("title", ""), url=d.get("link", ""), src=d.get("source", ""),
                          date=d.get("pubdate") or d.get("published") or d.get("updated") or d.get("date"),
                          snippet="" if feed.get("outlet_from_source") else d.get("description") or d.get("summary") or ""))
    return items

def collect_one(feed, base, now):
    t0 = time.time()
    h = dict(name=feed["name"], url=feed["url"], status="ok", items=0, kept=0, newest_h=None, ms=0, error=None, verified=feed.get("verified"))
    out = []
    try:
        items = parse_items(fetch_raw(feed["url"], base), feed)
        h["items"] = len(items)
        newest = None
        for it in items:
            ts = parse_date(it["date"])
            if ts is None:
                continue
            newest = max(newest or 0, ts)
            title = clean(it["title"])
            src = clean(it["src"]) if feed.get("outlet_from_source") else feed["name"]
            if feed.get("outlet_from_source"):
                if src and title.endswith(" - " + src):
                    title = title[: -len(src) - 3].rstrip()
                src = src or "Unknown"
            url = it["url"].strip()
            if not title or not url.startswith("http") or now - ts > WINDOW or ts > now + 3600:
                continue
            snippet = clean(it["snippet"])[:300]
            if feed.get("filter") and not (UAE_RE.search(title + " " + snippet) and BIZ_RE.search(title + " " + snippet)):
                continue
            out.append(dict(title=title, url=url, source=src, ts=ts, snippet=snippet, feed=feed["name"]))
        h["kept"] = len(out)
        if newest is not None:
            h["newest_h"] = round((now - newest) / 3600, 1)
        if h["items"] == 0:
            h["status"] = "empty"
        elif newest is not None and now - newest > STALE_HOURS * 3600:
            h["status"] = "stale"
    except Exception as e:
        h["status"], h["error"] = "error", f"{type(e).__name__}: {str(e)[:100]}"
    h["ms"] = int((time.time() - t0) * 1000)
    return h, (out if h["status"] == "ok" else [])

def collect(feeds, base, now):
    with ThreadPoolExecutor(10) as ex:
        res = list(ex.map(lambda f: collect_one(f, base, now), feeds))
    health = [r[0] for r in res]
    items, seen_url, seen_title = [], set(), set()
    for _, its in res:
        for it in its:                                   # drop exact duplicates across feeds
            ku, kt = canon(it["url"]), (" ".join(toks(it["title"])), it["source"].lower())
            if ku in seen_url or kt in seen_title:
                continue
            seen_url.add(ku); seen_title.add(kt)
            items.append(it)
    return health, items


# ---------- 2. group ----------
def vecs(items):
    df = Counter()
    for it in items:
        it["_t"] = set(toks(it["title"]))
        df.update(it["_t"])
    n = len(items)
    idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
    for it in items:
        v = {w: idf[w] for w in it["_t"]}
        it["_v"], it["_n"] = v, math.sqrt(sum(x * x for x in v.values())) or 1.0

def sim(a, b):
    if abs(a["ts"] - b["ts"]) > WINDOW:
        return 0.0
    shared = len(a["_t"] & b["_t"])
    if shared < MIN_SHARED:
        return 0.0
    cos = sum(x * b["_v"].get(w, 0) for w, x in a["_v"].items()) / (a["_n"] * b["_n"])
    if shared >= 4 and shared / min(len(a["_t"]), len(b["_t"])) >= 0.5:   # reworded wire copy: most of the shorter headline is shared
        cos = max(cos, SIM)
    return cos

def group(items):
    vecs(items)
    clusters = []
    for it in sorted(items, key=lambda i: i["ts"]):
        best, bs = None, 0.0
        for c in clusters:
            s = max(sim(m, it) for m in c)
            if s >= SIM and s > bs:
                best, bs = c, s
        if best is not None:
            best.append(it)
        else:
            clusters.append([it])
    changed = True                                       # merge clusters that later items bridged
    while changed:
        changed = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if max(sim(a, b) for a in clusters[i] for b in clusters[j]) >= MERGE_SIM:
                    clusters[i] += clusters.pop(j)
                    changed = True
                    break
            if changed:
                break
    return clusters

def make_story(c, weights, now):
    by = {}
    for m in sorted(c, key=lambda m: m["ts"]):          # one entry per outlet, earliest wins
        by.setdefault(m["source"].lower(), m)
    ms = list(by.values())
    w = lambda m: weights.get(m["source"].lower(), 1)
    lead = max(ms, key=lambda m: (w(m), -m["ts"]))
    first = min(c, key=lambda m: m["ts"])
    pick = lambda m: dict(title=m["title"], url=m["url"], source=m["source"])
    age_h = (now - max(m["ts"] for m in c)) / 3600
    return dict(id=hashlib.sha1(canon(first["url"]).encode()).hexdigest()[:10], lead=pick(lead),
                more=[pick(m) for m in sorted((m for m in ms if m is not lead), key=lambda m: -w(m))],
                ts=max(m["ts"] for m in c), n=len(ms), topic=tag(" ".join(m["title"] for m in c)), score=len(ms) * 1.5 + 6 * math.exp(-age_h / 10),
                _titles=[m["title"] for m in c], _snips=[m["snippet"] for m in c if m["snippet"]])


# ---------- 3. summaries (original wording only) ----------
RULES = """You write the one-to-two sentence blurb under a headline on a UAE business news page.
Rules:
- Say what happened and who is involved. Include the key number, place or date only if the material gives it.
- At most 35 words. Plain words and concrete verbs, like a person writing a quick note.
- Use your own wording. Never reuse a run of five or more words from the material. No quotation marks.
- Use only facts in the material. If it is thin, say less.
- Avoid: em dashes, "marks a", "pivotal", "testament", "landscape", "underscores", "highlights", "amid", "not just X but Y", lists of three, and any closing line about what it means.
Reply with the blurb only."""

def shingles(text, n=5):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}

def problem(summary, titles, snips):
    if not summary or len(summary) > 320:
        return "empty or too long"
    if BAD_PHRASES.search(summary):
        return "uses a banned phrase or dash"
    src = " ".join(titles + snips)
    if shingles(summary) & shingles(src):
        return "copies a run of five words from the source"
    s = set(toks(summary))
    for t in titles:
        tt = set(toks(t))
        if tt and len(s & tt) / len(s | tt) > 0.75:
            return "too close to a headline"
    return None

def call_model(prompt, key, model):
    body = json.dumps({"model": model, "max_tokens": 160, "system": RULES, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
        return "".join(b.get("text", "") for b in json.load(r)["content"]).strip()

def summarize_one(story, key, model):
    mat = "Headlines:\n" + "\n".join("- " + t for t in story["_titles"][:5])
    if story["_snips"]:
        mat += "\n\nExtra context (do not copy):\n" + "\n".join("- " + s for s in story["_snips"][:3])
    prompt, why = mat, None
    for _ in range(2):
        try:
            text = call_model(prompt, key, model).strip().strip('"')
        except Exception as e:
            print(f"  summary call failed for {story['id']}: {type(e).__name__}", file=sys.stderr)
            return None
        why = problem(text, story["_titles"], story["_snips"])
        if not why:
            return text
        prompt = mat + f"\n\nYour last attempt was rejected: {why}. Write it again in different words."
    print(f"  summary rejected for {story['id']}: {why}", file=sys.stderr)
    return None

def summarize(stories, cache, key, model):
    todo = [s for s in stories if s["id"] not in cache]
    if key and todo:
        with ThreadPoolExecutor(4) as ex:
            for s, text in zip(todo, ex.map(lambda s: summarize_one(s, key, model), todo)):
                if text:
                    cache[s["id"]] = {"text": text, "ts": int(time.time())}
    for s in stories:
        s["summary"] = cache.get(s["id"], {}).get("text", "")


# ---------- 4. checks ----------
def check_stories(stories):
    errs, urls = [], Counter()
    for s in stories:
        for x in [s["lead"]] + s["more"]:
            urls[canon(x["url"])] += 1
            if not x["title"] or not x["url"].startswith("http"):
                errs.append(f"bad link in story {s['id']}")
    errs += [f"duplicate link in two places: {u}" for u, c in urls.items() if c > 1]
    ids = Counter(s["id"] for s in stories)
    errs += [f"duplicate story id {i}" for i, c in ids.items() if c > 1]
    leads = [dict(title=s["lead"]["title"], ts=s["ts"]) for s in stories]
    vecs(leads)
    for i in range(len(leads)):
        for j in range(i + 1, len(leads)):
            if sim(leads[i], leads[j]) >= MERGE_SIM:
                errs.append(f"near-duplicate stories: {leads[i]['title']!r} / {leads[j]['title']!r}")
    return errs

def health_table(health):
    rows = [f"{'status':6} {'items':>5} {'kept':>4} {'newest_h':>8}  feed" ]
    for h in health:
        rows.append(f"{h['status']:6} {h['items']:>5} {h['kept']:>4} {str(h['newest_h']):>8}  {h['name']}" + (f"  [{h['error']}]" if h["error"] else ""))
    return "\n".join(rows)


# ---------- 5. build the page ----------
TOPICS = [
    ("Energy", r"\b(oil|gas\b|adnoc|energy|opec|solar|masdar|power|fuel|barrel|petrol|refiner)"),
    ("Property", r"\b(propert|real estate|emaar|aldar|damac|tower|villa|apartment|developer|housing|rent|mortgage)"),
    ("Startups", r"\b(startup|fintech|funding|raises|venture|seed round|series [abc]\b|unicorn|g42|ai\b)"),
    ("Banking", r"\b(bank|loan|lender|credit|sukuk|bond|dirham|insur|interest rate|base rate|payment)"),
    ("Markets", r"\b(stock|share|dfm|adx|index|ipo|listing|investor|equity|fund|dividend|profit|earning|market)"),
    ("Trade", r"\b(trade|export|import|cargo|ports?\b|freight|logistic|airline|aviation|shipping|free zone|tourism|hotel|airport)"),
]

def tag(text):
    for name, rx in TOPICS:
        if re.search(rx, text, re.I):
            return name
    return "Business"

CSS = """:root{--bg:#f7f6f3;--card:#fff;--ink:#1f1f1e;--mute:#787774;--line:#e4e2dd;--link:#1f6c9f;--acc:#0f5c4d}
@media(prefers-color-scheme:dark){:root{--bg:#171716;--card:#1f1f1e;--ink:#ecebe7;--mute:#9b9a97;--line:#2e2e2c;--link:#7db7de;--acc:#5fc2a8}}
.t-energy{--b:#fdebec;--c:#9f2f2d}.t-property{--b:#fbf3db;--c:#956400}.t-startups{--b:#efe8fa;--c:#5b3a99}
.t-banking{--b:#e1f3fe;--c:#1f6c9f}.t-markets{--b:#edf3ec;--c:#346538}.t-trade{--b:#fdeee3;--c:#9a4d14}.t-business{--b:#eeede9;--c:#5c5b57}
@media(prefers-color-scheme:dark){.t-energy{--b:#3a2223;--c:#f0a9a7}.t-property{--b:#38301a;--c:#e6c36b}.t-startups{--b:#2d2640;--c:#c3aef0}
.t-banking{--b:#1d3040;--c:#8cc4ea}.t-markets{--b:#233023;--c:#9fcca2}.t-trade{--b:#3a2a1d;--c:#efb98c}.t-business{--b:#2a2a28;--c:#b9b8b3}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 "SF Pro Text","Helvetica Neue",system-ui,sans-serif}
a{color:inherit}.wrap{max-width:1080px;margin:0 auto;padding:0 16px 56px}
.top{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);font:12px/1.4 "SF Mono","Geist Mono",ui-monospace,monospace;color:var(--mute)}
.mast{padding:26px 0 18px}
h1{font:700 40px/1 "Newsreader","Lyon Text",Georgia,serif;letter-spacing:-.035em;margin:0}
h1 i{font-style:normal;color:var(--acc)}
.tag{margin:8px 0 0;color:var(--mute);font-size:15px}
.demo{background:#fbf3db;color:#956400;border-radius:8px;padding:8px 12px;font-size:13px;margin:0 0 14px}
.chips{display:flex;gap:8px;overflow-x:auto;padding:12px 0;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);z-index:2;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;font:600 13px/1 inherit;font-family:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:9999px;padding:8px 14px;cursor:pointer}
.chip[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.chip small{opacity:.6;font-weight:500;margin-left:4px}
.grid{display:block;padding-top:6px}
article{padding:20px 0;border-bottom:1px solid var(--line)}article[hidden]{display:none}
.kick{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--mute);margin:0 0 8px}
.pill{background:var(--b);color:var(--c);border-radius:9999px;padding:2px 10px;font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase}
.kick b{color:var(--ink);font-weight:600}
h2{font:600 22px/1.25 "Newsreader","Lyon Text",Georgia,serif;letter-spacing:-.02em;margin:0 0 8px}
h2 a{text-decoration:none}h2 a:hover{color:var(--link)}
.sum{margin:0 0 10px}
.more{margin:0;font-size:14px;color:var(--mute)}.more a{color:var(--link);text-decoration:none}.more a:hover{text-decoration:underline}
.n{font-size:12px;color:var(--mute)}
.hero{padding:26px 0 28px}
.hero h2{font-size:30px;line-height:1.15;letter-spacing:-.03em}
.hero .sum{font-size:18px;line-height:1.55}
aside{display:none}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:20px}
.box h3{margin:0 0 12px;font:600 12px/1 inherit;font-family:inherit;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.box ol{margin:0;padding:0;list-style:none;counter-reset:r}
.box li{counter-increment:r;display:grid;grid-template-columns:22px 1fr;gap:6px;padding:10px 0;border-top:1px solid var(--line);font-size:15px;line-height:1.35}
.box li:first-child{border-top:0;padding-top:0}
.box li:before{content:counter(r);font:600 13px/1.5 "SF Mono",ui-monospace,monospace;color:var(--acc)}
.box li a{text-decoration:none}.box li a:hover{color:var(--link)}.box li span{display:block;font-size:12px;color:var(--mute);margin-top:2px}
.box p{margin:0;font-size:14px;color:var(--mute)}
footer{margin-top:28px;color:var(--mute);font-size:13px}footer a{color:var(--mute)}
@media(min-width:900px){
h1{font-size:56px}.mast{padding:40px 0 26px}.hero h2{font-size:40px}.hero .sum{font-size:19px}h2{font-size:24px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:56px}aside{display:block;padding-top:22px;position:sticky;top:64px;align-self:start}}"""

JS = """var chips=document.querySelectorAll('.chip'),arts=document.querySelectorAll('article[data-t]');
chips.forEach(function(c){c.addEventListener('click',function(){chips.forEach(function(x){x.setAttribute('aria-pressed',x===c?'true':'false')});arts.forEach(function(a){a.hidden=c.dataset.t!=='All'&&a.dataset.t!==c.dataset.t})})});
document.querySelectorAll('time[datetime]').forEach(function(t){var m=(Date.now()-Date.parse(t.dateTime))/6e4;t.textContent=m<2?'just now':m<60?Math.round(m)+'m ago':m<1440?Math.round(m/60)+'h ago':Math.round(m/1440)+'d ago'})"""

def ago(now, ts):
    m = (now - ts) / 60
    return "just now" if m < 2 else f"{int(m)}m ago" if m < 60 else f"{int(m / 60)}h ago" if m < 1440 else f"{int(m / 1440)}d ago"

def render(stories, health, now, demo):
    e = html.escape
    ok = sum(1 for h in health if h["status"] == "ok")
    outlets = {x["source"].lower() for s in stories for x in [s["lead"]] + s["more"]}
    counts = Counter(s["topic"] for s in stories)
    chips = f'<button class="chip" data-t="All" aria-pressed="true">All<small>{len(stories)}</small></button>' + "".join(
        f'<button class="chip" data-t="{e(t)}" aria-pressed="false">{e(t)}<small>{c}</small></button>' for t, c in counts.most_common())
    body = []
    for i, s in enumerate(stories):
        l = s["lead"]
        more = ", ".join(f'<a href="{e(x["url"])}" rel="noopener">{e(x["source"])}</a>' for x in s["more"][:6])
        body.append(
            f'<article class="{"hero" if i == 0 else ""}" data-t="{e(s["topic"])}">'
            f'<p class="kick"><span class="pill t-{s["topic"].lower()}">{e(s["topic"])}</span><b>{e(l["source"])}</b>'
            f'<time datetime="{datetime.fromtimestamp(s["ts"], timezone.utc).isoformat()}">{ago(now, s["ts"])}</time>'
            + (f'<span class="n">{s["n"]} outlets</span>' if s["n"] > 1 else "") + "</p>"
            f'<h2><a href="{e(l["url"])}" rel="noopener">{e(l["title"])}</a></h2>'
            + (f'<p class="sum">{e(s["summary"])}</p>' if s.get("summary") else "")
            + (f'<p class="more">More: {more}</p>' if more else "") + "</article>")
    top = sorted(stories, key=lambda s: (-s["n"], -s["ts"]))[:5]
    side = "".join(f'<li><div><a href="{e(s["lead"]["url"])}" rel="noopener">{e(s["lead"]["title"])}</a><span>{s["n"]} outlet{"" if s["n"] == 1 else "s"} &middot; {e(s["topic"])}</span></div></li>' for s in top)
    dt = datetime.fromtimestamp(now, GST)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(SITE_NAME)}</title><meta name="description" content="{e(TAGLINE)}">
<style>{CSS}</style></head><body><div class="wrap">
<div class="top"><span>{dt.strftime("%A, %d %B %Y")}</span><span>Updated {dt.strftime("%H:%M")} GST</span></div>
<header class="mast"><h1>UAE Ledger<i>.</i></h1><p class="tag">{e(TAGLINE)}</p></header>
{'<p class="demo">Demo data. These stories are fictional test fixtures, not real news.</p>' if demo else ''}
<nav class="chips" aria-label="Topics">{chips}</nav>
<div class="grid"><main>{''.join(body)}</main>
<aside><div class="box"><h3>Most covered now</h3><ol>{side}</ol></div>
<div class="box"><h3>About</h3><p>{len(stories)} stories from {len(outlets)} outlets, {ok} feeds live. Headlines and links belong to their publishers. Blurbs are written by an AI and can contain mistakes, so check the source. <a href="health.json">Feed health</a></p></div></aside></div>
<footer><p>Headlines and links belong to their publishers. Blurbs are written by an AI and can contain mistakes.</p></footer>
</div><script>{JS}</script></body></html>"""


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", default=os.path.join(os.path.dirname(__file__), "feeds.json"))
    ap.add_argument("--out", default="site")
    ap.add_argument("--data", default="data/summaries.json")
    ap.add_argument("--min-feeds", type=int, default=10)
    ap.add_argument("--min-stories", type=int, default=8)
    ap.add_argument("--demo", action="store_true", help="mark the page as fixture data")
    a = ap.parse_args()

    cfg = json.load(open(a.feeds))
    base, now = os.path.dirname(os.path.abspath(a.feeds)), int(time.time())
    print(f"Collecting from {len(cfg['feeds'])} feeds...")
    health, items = collect(cfg["feeds"], base, now)
    print(health_table(health))
    ok = sum(1 for h in health if h["status"] == "ok")
    clusters = group(items)
    stories = [make_story(c, cfg.get("outlet_weights", {}), now) for c in clusters]
    stories.sort(key=lambda s: -s["score"])
    stories = stories[:MAX_STORIES]

    cache = json.load(open(a.data)) if os.path.exists(a.data) else {}
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("No ANTHROPIC_API_KEY: showing cached blurbs only, headlines for the rest.")
    summarize(stories, cache, key, os.environ.get("SUMMARY_MODEL", "claude-haiku-5-5"))

    errs = check_stories(stories)
    if ok < a.min_feeds:
        errs.append(f"only {ok} feeds healthy, need {a.min_feeds}")
    if len(stories) < a.min_stories:
        errs.append(f"only {len(stories)} stories, need {a.min_stories}")
    summary = f"{len(items)} headlines -> {len(clusters)} stories, {ok}/{len(health)} feeds healthy"
    if errs:
        print("NOT PUBLISHING (last good page stays live):\n  " + "\n  ".join(errs) + "\n" + summary, file=sys.stderr)
        sys.exit(1)

    live = {s["id"] for s in stories}                    # keep cache small: current stories + last 7 days
    cache = {k: v for k, v in cache.items() if k in live or now - v["ts"] < 7 * 86400}
    os.makedirs(os.path.dirname(a.data) or ".", exist_ok=True)
    json.dump(cache, open(a.data, "w"), indent=1)
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "index.html"), "w").write(render(stories, health, now, a.demo))
    open(os.path.join(a.out, ".nojekyll"), "w").close()
    json.dump(dict(updated=now, feeds=health), open(os.path.join(a.out, "health.json"), "w"), indent=1)
    json.dump([{k: v for k, v in s.items() if not k.startswith("_")} for s in stories], open(os.path.join(a.out, "stories.json"), "w"), indent=1)
    print("Published: " + summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(f"### Feed health\n{summary}\n\n```\n{health_table(health)}\n```\n")

if __name__ == "__main__":
    main()
