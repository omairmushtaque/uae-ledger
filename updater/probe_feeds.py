"""Probe candidate feeds: status, parse, item count, newest item age."""
import sys, json, ssl, os, re, time, urllib.request, concurrent.futures as cf
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

UA = "Mozilla/5.0 (compatible; UAEFinanceHubBot/0.1)"
bundle = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
ctx = ssl.create_default_context(cafile=bundle if os.path.exists(bundle) else None)

def age_hours(s):
    if not s: return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try: d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception: return None
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - d).total_seconds() / 3600, 1)

def probe(c):
    name, url = c
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            body = r.read(2_000_000); code = r.status
    except Exception as e:
        return dict(name=name, url=url, ok=False, err=str(e)[:80])
    txt = body.decode("utf-8", "replace")
    if txt.lstrip().startswith("{"):
        try:
            j = json.loads(txt); arts = j.get("articles") or []
            return dict(name=name, url=url, ok=bool(arts), kind="json", items=len(arts), err=None if arts else "no articles")
        except Exception as e:
            return dict(name=name, url=url, ok=False, err="bad json")
    try:
        root = ET.fromstring(re.sub(r"^\s+", "", txt).encode())
    except Exception as e:
        return dict(name=name, url=url, ok=False, err="not xml: " + txt[:40].replace("\n"," "))
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    newest = None
    for it in items[:5]:
        for tag in ("pubDate", "{http://www.w3.org/2005/Atom}updated", "{http://www.w3.org/2005/Atom}published"):
            e = it.find(tag)
            if e is not None and e.text:
                a = age_hours(e.text)
                if a is not None and (newest is None or a < newest): newest = a
    return dict(name=name, url=url, ok=len(items) > 0, kind="rss", items=len(items), newest_h=newest, secs=round(time.time()-t,1))

cands = [l.strip().split("|", 1) for l in open(sys.argv[1]) if l.strip() and not l.startswith("#")]
with cf.ThreadPoolExecutor(12) as ex:
    res = list(ex.map(probe, cands))
for r in res:
    flag = "OK  " if r["ok"] else "FAIL"
    extra = f"items={r.get('items')} newest={r.get('newest_h')}h" if r["ok"] else r.get("err")
    print(f"{flag} {r['name']:34} {extra}")
json.dump(res, open(sys.argv[2], "w"), indent=1)
