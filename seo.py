"""Lippu illaksi – hakukoneille näkyvät sivut.

Lukee data.json-tiedoston ja tekee siitä tavallisia HTML-sivuja, jotka Google
löytää ja ymmärtää ilman JavaScriptiä:

  /tanaan/  /huomenna/          päivän näytökset
  /teatterit/                   teatterilista
  /teatteri/<teatteri>/         teatterin näytökset lähipäivinä
  /teatteri/<teatteri>/<esitys>/  esityssivu + tapahtumatiedot (schema.org Event)
  sitemap.xml, robots.txt, 404.html, tyyli.css

Esityssivut säilyvät 90 päivää viimeisen näytöksen jälkeen (seo-tila.json),
jotta Googlen jo löytämät osoitteet eivät katoa heti.
Ajetaan GitHub Actionsissa update.py:n jälkeen.
"""
import datetime as dt
import html
import json
import os
import re
import shutil
from zoneinfo import ZoneInfo

BASE = "https://lippuillaksi.fi"
TZ = ZoneInfo("Europe/Helsinki")
KEEP_DAYS = 90
STATE_FILE = "seo-tila.json"
WD = ["ma", "ti", "ke", "to", "pe", "la", "su"]
WD_LONG = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]
AVAIL = {"many": ("many", "Hyvin tilaa", "https://schema.org/InStock"),
         "few": ("few", "Vähän jäljellä", "https://schema.org/LimitedAvailability"),
         "sold": ("sold", "Loppuunmyyty", "https://schema.org/SoldOut"),
         "unknown": ("unknown", "Tarkista saatavuus", None)}

e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def slug(s):
    s = s.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("å", "a"), ("é", "e")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "esitys"


def fdate(d):
    return f"{WD[d.weekday()]} {d.day}.{d.month}."


def fprice(p):
    return (f"{p:.2f}".replace(".", ",") if p % 1 else str(int(p))) + " €"


def fdur(m, inter):
    if not m:
        return None
    h, r = divmod(int(m), 60)
    s = " ".join(x for x in (f"{h} t" if h else "", f"{r} min" if r else "") if x)
    return s + (", sis. väliajan" if inter is True else ", ei väliaikaa" if inter is False else "")


CSS = """:root{--bg:#F6F1E7;--surface:#FFFCF6;--ink:#221C17;--muted:#6B5F55;--line:#E3D9C9;--chip:#EEE6D8;--accent:#A3123A;--accent-ink:#FFF;--accent-soft:#F3E0E0;--ok:#1F7A4D;--ok-soft:#E2F0E4;--warn:#A55300;--warn-soft:#F8E8D2;--sold:#6F645A;--sold-soft:#EDE7DD;--f-display:"Schibsted Grotesk","Helvetica Neue","Segoe UI",system-ui,sans-serif;--f-body:"Schibsted Grotesk","Helvetica Neue","Segoe UI",system-ui,-apple-system,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#16120E;--surface:#201A15;--ink:#F3ECE2;--muted:#B3A696;--line:#3A3128;--chip:#2C251E;--accent:#F0567A;--accent-ink:#1A0710;--accent-soft:#3A1A22;--ok:#4CC38A;--ok-soft:#17301F;--warn:#F0A243;--warn-soft:#3A2A14;--sold:#968A7D;--sold-soft:#27211B;color-scheme:dark}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--f-body);font-size:16px;line-height:1.5}
a{color:var(--accent)}
.wrap{max-width:920px;margin:0 auto;padding:24px 16px 56px;display:flex;flex-direction:column;gap:20px}
.main{display:flex;flex-direction:column;gap:20px;min-width:0}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 20px}
.logo{font-family:var(--f-display);font-weight:800;font-size:1.6rem;line-height:1;letter-spacing:-.02em;color:var(--ink);text-decoration:none}
.logo span{color:var(--accent)}
.logo img{height:1.9em;width:auto;vertical-align:-.6em;margin-right:.2em}
.nav{display:flex;flex-wrap:wrap;gap:6px}
.nav a{padding:6px 12px;border-radius:999px;background:var(--chip);color:var(--ink);text-decoration:none;font-size:.9rem;font-weight:500}
.nav a[aria-current]{background:var(--ink);color:var(--bg)}
.crumbs{font-size:.85rem;color:var(--muted);margin:0}.crumbs a{color:var(--muted)}
h1{font-family:var(--f-display);font-weight:800;font-size:clamp(1.7rem,5vw,2.4rem);line-height:1.1;letter-spacing:-.02em;margin:0;text-wrap:balance}
.lead{margin:6px 0 0;color:var(--muted);font-size:1.02rem;max-width:66ch}
h2{font-family:var(--f-display);font-weight:600;font-size:1.25rem;margin:10px 0 0;padding-bottom:8px;border-bottom:1px solid var(--line)}
.list{display:flex;flex-direction:column;gap:10px}
.show{display:grid;grid-template-columns:5rem minmax(0,1fr) auto;gap:6px 18px;align-items:start;padding:16px 18px;background:var(--surface);border:1px solid var(--line);border-radius:14px}
.time{font-family:var(--f-display);font-weight:800;font-size:1.5rem;line-height:1.1;font-variant-numeric:lining-nums}
.time small{display:block;font-family:var(--f-body);font-size:.75rem;font-weight:600;color:var(--muted)}
.info{min-width:0;display:flex;flex-direction:column;gap:4px}
.info h3{margin:0;font-size:1.12rem;line-height:1.25}.info h3 a{color:var(--ink);text-decoration:none}.info h3 a:hover{text-decoration:underline}
.venue{margin:0;color:var(--muted);font-size:.93rem}
.meta{display:flex;flex-wrap:wrap;gap:6px}
.tag{font-size:.8rem;padding:2px 9px;border-radius:6px;background:var(--chip)}
.tag.genre{background:var(--accent-soft);color:var(--accent);font-weight:600}
.flag{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--warn)}
.buy{display:flex;flex-direction:column;align-items:flex-end;gap:8px;text-align:right}
.avail{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}
.avail::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.avail.many{color:var(--ok);background:var(--ok-soft)}.avail.few{color:var(--warn);background:var(--warn-soft)}
.avail.sold{color:var(--sold);background:var(--sold-soft)}.avail.unknown{color:var(--muted);background:var(--chip)}
.avail.unknown::before{background:transparent;border:1.5px solid currentColor;width:6px;height:6px}
.price{font-size:.92rem;color:var(--muted)}.price b{color:var(--ink)}
.btn{display:inline-block;border-radius:10px;padding:9px 16px;font-weight:700;background:var(--accent);color:var(--accent-ink);text-decoration:none;white-space:nowrap}
.btn.ghost{background:var(--chip);color:var(--ink)}.btn.off{background:var(--sold-soft);color:var(--sold)}
.show.is-sold{opacity:.7}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.card p{margin:0 0 8px;max-width:66ch}.card p:last-child{margin:0}
.facts{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;margin:0}.facts dt{color:var(--muted)}.facts dd{margin:0;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;list-style:none;margin:0;padding:0}
.grid li a{display:block;height:100%;padding:14px 16px;background:var(--surface);border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--ink)}
.grid li a:hover{border-color:var(--muted)}.grid b{display:block}.grid span{font-size:.88rem;color:var(--muted)}
.empty{padding:28px 20px;text-align:center;border:1px dashed var(--line);border-radius:14px;color:var(--muted)}
.cta{display:flex;flex-wrap:wrap;gap:8px}
footer{color:var(--muted);font-size:.85rem;border-top:1px solid var(--line);padding-top:14px}footer p{margin:0 0 6px;max-width:70ch}
@media (max-width:640px){.show{grid-template-columns:3.8rem minmax(0,1fr);padding:14px}.time{font-size:1.25rem}
.buy{grid-column:1/-1;flex-direction:row;flex-wrap:wrap;align-items:center;justify-content:space-between;border-top:1px solid var(--line);padding-top:10px}}
"""


def page(path, title, desc, body, nav="", jsonld=None, noindex=False):
    canon = BASE + path
    dump = lambda x: json.dumps(x, ensure_ascii=False).replace("</", "<\\/")
    ld = "".join(f'<script type="application/ld+json">{dump(x)}</script>\n' for x in (jsonld or []))
    navs = [("/", "Kaikki päivät"), ("/tanaan/", "Tänään"), ("/huomenna/", "Huomenna"), ("/teatterit/", "Teatterit")]
    cur = ' aria-current="page"'
    nav_html = "".join(f'<a href="{h}"{cur if h == nav else ""}>{t}</a>' for h, t in navs)
    return f"""<!doctype html>
<html lang="fi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc[:300])}">
{'<meta name="robots" content="noindex">' if noindex else f'<link rel="canonical" href="{e(canon)}">'}
<meta property="og:type" content="website">
<meta property="og:site_name" content="Lippu illaksi">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc[:300])}">
<meta property="og:url" content="{e(canon)}">
<meta property="og:locale" content="fi_FI">
<link rel="icon" href="/favicon.ico?v=6" sizes="48x48">
<link rel="icon" href="/favicon.svg?v=6" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png?v=6">
<meta property="og:image" content="https://lippuillaksi.fi/logo-512.png">
<meta name="color-scheme" content="light dark">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600;700;800&display=swap">
<link rel="stylesheet" href="/tyyli.css">
{ld}</head>
<body>
<div class="wrap">
<header class="top"><a class="logo" href="/"><img src="/logo.svg?v=6" alt="" width="28" height="40">Lippu <span>illaksi</span></a><nav class="nav" aria-label="Päävalikko">{nav_html}</nav></header>
<main class="main">
{body}
</main>
<footer>
<p>Lipputilanne haetaan teattereiden ja lipunmyyjien sivuilta kerran aamussa, ja se voi muuttua nopeasti. Tarkista lopullinen tilanne ja hinta lipunmyyjältä. Hinnat ovat halvimpia aikuisten lippuja ilman palvelumaksuja.</p>
<p><a href="/">Lippu illaksi</a> kokoaa pääkaupunkiseudun teattereiden lähipäivien näytökset yhdelle sivulle.</p>
</footer>
</div>
</body>
</html>
"""


def write(path, content):
    fp = path.strip("/") + "/index.html" if path.endswith("/") and path != "/" else path.strip("/")
    os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)


def show_html(s, p, with_title=True, with_date=False):
    cls, label, _ = AVAIL.get(s.get("avail"), AVAIL["unknown"])
    if cls == "few" and s.get("seats"):
        label = f"Vain {s['seats']} paikkaa"
    d = dt.date.fromisoformat(s["date"])
    tags = [f'<span class="tag genre">{e(p["genre"])}</span>']
    if fdur(p.get("dur"), p.get("inter")):
        tags.append(f'<span class="tag">{e(fdur(p.get("dur"), p.get("inter")))}</span>')
    if p.get("lang"):
        tags.append(f'<span class="tag">{"Ei puhetta" if p["lang"] == "ei puhetta" else "Kieli: " + e(p["lang"])}</span>')
    subs = s.get("subs", p.get("subs"))
    if subs:
        tags.append(f'<span class="tag">Tekstitys: {e(subs)}</span>')
    title = (f'<h3><a href="{p["_path"]}">{e(p["title"])}</a></h3>' if with_title else "")
    venue = (f'{e(p["theatre"])} · {e(p["stage"])} · {e(p["city"])}' if with_title else f'{e(p["stage"])}')
    if cls == "sold":
        btn = '<span class="btn off">Ei lippuja</span>'
    else:
        btn = (f'<a class="btn{" ghost" if cls == "unknown" else ""}" href="{e(s["url"])}" rel="noopener nofollow" '
               f'target="_blank">{"Katso paikat ↗" if cls == "unknown" else "Osta liput ↗"}</a>')
    return f"""<article class="show{" is-sold" if cls == "sold" else ""}">
<div class="time">{e(s["time"])}{f"<small>{fdate(d)}</small>" if with_date else ""}{f'<small class="flag">{e(s["note"])}</small>' if s.get("note") else ""}</div>
<div class="info">{title}<p class="venue">{venue}</p><div class="meta">{"".join(tags)}</div></div>
<div class="buy"><span class="avail {cls}">{e(label)}</span>{f'<span class="price">alk. <b>{fprice(s["price"])}</b></span>' if isinstance(s.get("price"), (int, float)) else ""}{btn}</div>
</article>"""


def event_ld(s, p):
    h, m = s["time"].split(".")
    start = dt.datetime.fromisoformat(f"{s['date']}T{int(h):02d}:{m}").replace(tzinfo=TZ)
    ev = {"@context": "https://schema.org", "@type": "TheaterEvent", "name": p["title"],
          "startDate": start.isoformat(), "eventStatus": "https://schema.org/EventScheduled",
          "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
          "location": {"@type": "PerformingArtsTheater", "name": f'{p["theatre"]}, {p["stage"]}' if p["stage"] != p["theatre"] else p["theatre"],
                       "address": {"@type": "PostalAddress", "addressLocality": p["city"], "addressCountry": "FI"}},
          "organizer": {"@type": "Organization", "name": p["theatre"], **({"url": p["page"]} if p.get("page") else {})},
          "url": BASE + p["_path"], "inLanguage": {"suomi": "fi", "ruotsi": "sv", "englanti": "en"}.get(p.get("lang"), "fi")}
    if p.get("desc"):
        ev["description"] = p["desc"]
    if p.get("dur"):
        ev["endDate"] = (start + dt.timedelta(minutes=int(p["dur"]))).isoformat()
    offer = {"@type": "Offer", "url": s["url"], "priceCurrency": "EUR"}
    if isinstance(s.get("price"), (int, float)):
        offer["price"] = s["price"]
    av = AVAIL.get(s.get("avail"), AVAIL["unknown"])[2]
    if av:
        offer["availability"] = av
    ev["offers"] = offer
    return ev


def crumbs_ld(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": BASE + u}
                                for i, (n, u) in enumerate(items)]}


def main():
    data = json.load(open("data.json", encoding="utf-8"))
    today = dt.datetime.now(TZ).date()
    try:
        state = json.load(open(STATE_FILE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}

    theatres = {t["name"]: {**t, "slug": slug(t["name"])} for t in data["theatres"]}
    prods = {}
    for pid, p in data["productions"].items():
        p = {k: html.unescape(v) if isinstance(v, str) else v for k, v in p.items()}
        st = re.sub(r"^" + re.escape(p["theatre"]) + r",?\s*", "", p.get("stage") or "", flags=re.I).strip()
        p["stage"] = st[:1].upper() + st[1:] if st else p["theatre"]
        th =theatres.setdefault(p["theatre"], {"name": p["theatre"], "city": p["city"], "slug": slug(p["theatre"])})
        p = dict(p, _path=f'/teatteri/{th["slug"]}/{slug(p["title"])}/', _shows=[])
        prods[pid] = p
    shows = [s for s in data["showings"] if s["p"] in prods and s["date"] >= today.isoformat()]
    shows.sort(key=lambda s: (s["date"], s["time"], prods[s["p"]]["title"]))
    for s in shows:
        prods[s["p"]]["_shows"].append(s)

    # seo-tila: muista esityssivut 90 päivää viimeisen näytöksen jälkeen
    for p in prods.values():
        if p["_shows"]:
            keep = {k: v for k, v in p.items() if not k.startswith("_")}
            state[p["_path"]] = {**keep, "last": p["_shows"][-1]["date"]}
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    state = {k: v for k, v in state.items() if v["last"] >= cutoff}
    active = {p["_path"] for p in prods.values() if p["_shows"]}

    # vanhat sivut pois, tehdään kaikki uudelleen
    for d in ("tanaan", "huomenna", "teatterit", "teatteri"):
        shutil.rmtree(d, ignore_errors=True)
    with open("tyyli.css", "w", encoding="utf-8") as f:
        f.write(CSS)
    urls = [("/", today.isoformat(), "1.0")]

    # päiväsivut
    for path, name, d in (("/tanaan/", "Tänään", today), ("/huomenna/", "Huomenna", today + dt.timedelta(days=1))):
        day = [s for s in shows if s["date"] == d.isoformat()]
        free = [s for s in day if s.get("avail") != "sold"]
        word = "tänään" if name == "Tänään" else "huomenna"
        datestr = f"{WD_LONG[d.weekday()]} {d.day}.{d.month}.{d.year}"
        title = f"Teatteri {word} {WD[d.weekday()]} {d.day}.{d.month}. – vapaat liput pääkaupunkiseudulla | Lippu illaksi"
        desc = (f"Teatteriin {word}? {len(free)} näytöstä {datestr}, joihin saa vielä lippuja Helsingissä, Espoossa ja Vantaalla. "
                + ", ".join(dict.fromkeys(prods[s["p"]]["title"] for s in free[:6])) + ".")
        body = f"""<div><h1>Teatteri {word}</h1>
<p class="lead">{datestr.capitalize()} · {len(free)} näytöstä pääkaupunkiseudulla, joihin voi vielä saada lippuja{f" · {len(day) - len(free)} loppuunmyyty" if len(day) > len(free) else ""}.</p></div>
<div class="list">{"".join(show_html(s, prods[s["p"]]) for s in day) or '<div class="empty">Tälle päivälle ei löytynyt näytöksiä. Katso <a href="/">muut päivät</a>.</div>'}</div>
<p class="cta"><a class="btn ghost" href="/">Kaikki lähipäivät ja suodattimet →</a></p>"""
        write(path, page(path, title, desc, body, nav=path))
        urls.append((path, today.isoformat(), "0.9"))

    # teatterilista
    tlist = sorted(theatres.values(), key=lambda t: (t["city"] != "Helsinki", t["city"], t["name"]))
    count = lambda t: sum(1 for s in shows if prods[s["p"]]["theatre"] == t["name"] and s.get("avail") != "sold")
    items = "".join(f'<li><a href="/teatteri/{t["slug"]}/"><b>{e(t["name"])}</b><span>{e(t["city"])} · '
                    f'{f"{count(t)} näytöstä lähipäivinä" if count(t) else "ei näytöksiä lähipäivinä"}</span></a></li>' for t in tlist)
    body = f"""<div><h1>Teatterit</h1><p class="lead">Pääkaupunkiseudun teatterit, joiden lähipäivien näytökset ja lipputilanteen Lippu illaksi kokoaa.</p></div>
<ul class="grid">{items}</ul>"""
    write("/teatterit/", page("/teatterit/", "Pääkaupunkiseudun teatterit – ohjelmisto ja vapaat liput | Lippu illaksi",
                              "Helsingin, Espoon ja Vantaan teatterit yhdellä sivulla: " + ", ".join(t["name"] for t in tlist[:8]) + " ja muut.",
                              body, nav="/teatterit/"))
    urls.append(("/teatterit/", today.isoformat(), "0.7"))

    # teatterisivut
    for t in tlist:
        tp = f'/teatteri/{t["slug"]}/'
        ts = [s for s in shows if prods[s["p"]]["theatre"] == t["name"]]
        titles = list(dict.fromkeys(prods[s["p"]]["title"] for s in ts))
        groups = ""
        for d in sorted({s["date"] for s in ts}):
            dd = dt.date.fromisoformat(d)
            label = "Tänään" if dd == today else "Huomenna" if dd == today + dt.timedelta(days=1) else WD_LONG[dd.weekday()].capitalize()
            groups += f'<h2>{label} {dd.day}.{dd.month}.</h2><div class="list">' + "".join(
                show_html(s, prods[s["p"]]) for s in ts if s["date"] == d) + "</div>"
        older = sorted((v for k, v in state.items() if v["theatre"] == t["name"] and k not in active), key=lambda v: v["title"])
        if older:
            groups += "<h2>Aiemmin ohjelmistossa</h2><ul class=\"grid\">" + "".join(
                f'<li><a href="/teatteri/{t["slug"]}/{slug(v["title"])}/"><b>{e(v["title"])}</b><span>ei näytöksiä lähipäivinä</span></a></li>'
                for v in older) + "</ul>"
        if not ts:
            groups = (f'<div class="empty">Teatterin näytöksiä ei ole juuri nyt saatavilla. '
                      f'{e(t.get("note", ""))}.</div>') + groups
        desc = (f"{t['name']} ({t['city']}): lähipäivien näytökset ja lipputilanne. "
                + (f"Ohjelmistossa mm. {', '.join(titles[:5])}." if titles else ""))
        body = f"""<p class="crumbs"><a href="/teatterit/">Teatterit</a> › {e(t["name"])}</p>
<div><h1>{e(t["name"])}</h1><p class="lead">{e(t["city"])} · lähipäivien näytökset ja lipputilanne{f" · {len(ts)} näytöstä" if ts else ""}.</p></div>
{groups}"""
        write(tp, page(tp, f"{t['name']} – näytökset ja vapaat liput lähipäivinä | Lippu illaksi", desc, body,
                       jsonld=[crumbs_ld([("Teatterit", "/teatterit/"), (t["name"], tp)])]))
        urls.append((tp, today.isoformat(), "0.7"))

    # esityssivut (myös hetkeksi ohjelmistosta poistuneet)
    for path, v in state.items():
        p = next((x for x in prods.values() if x["_path"] == path and x["_shows"]), None)
        p = p or dict(v, _path=path, _shows=[])
        t = theatres.get(p["theatre"], {"slug": path.split("/")[2], "name": p["theatre"]})
        tp = f'/teatteri/{t["slug"]}/'
        facts = [("Teatteri", f'<a href="{tp}">{e(p["theatre"])}</a>'), ("Näyttämö", e(p["stage"])), ("Kaupunki", e(p["city"])),
                 ("Lajityyppi", e(p["genre"]))]
        if fdur(p.get("dur"), p.get("inter")):
            facts.append(("Kesto", e(fdur(p.get("dur"), p.get("inter")))))
        if p.get("lang"):
            facts.append(("Kieli", e(p["lang"])))
        if p.get("subs"):
            facts.append(("Tekstitys", e(p["subs"])))
        dates = ", ".join(dict.fromkeys(fdate(dt.date.fromisoformat(s["date"])) for s in p["_shows"]))
        if p["_shows"]:
            free = [s for s in p["_shows"] if s.get("avail") != "sold"]
            listing = (f'<h2>Näytökset lähipäivinä</h2><div class="list">'
                       + "".join(show_html(s, p, with_title=False, with_date=True) for s in p["_shows"]) + "</div>")
            desc = f"{p['title']} ({p['theatre']}): liput ja lipputilanne. Näytökset {dates}." + (f" {p['desc']}" if p.get("desc") else "")
            lead = f"{len(free)} näytöstä lähipäivinä, joihin voi vielä saada lippuja." if free else "Lähipäivien näytökset on myyty loppuun."
        else:
            last = dt.date.fromisoformat(v["last"])
            listing = (f'<div class="empty">Esitykselle ei ole näytöksiä lähipäivinä (viimeksi {last.day}.{last.month}.{last.year}). '
                       f'Katso <a href="{tp}">teatterin muut näytökset</a> tai <a href="/">kaikki lähipäivien näytökset</a>.</div>')
            desc = f"{p['title']} ({p['theatre']}). Ei näytöksiä lähipäivinä." + (f" {p['desc']}" if p.get("desc") else "")
            lead = "Ei näytöksiä lähipäivinä."
        body = f"""<p class="crumbs"><a href="/teatterit/">Teatterit</a> › <a href="{tp}">{e(p["theatre"])}</a> › {e(p["title"])}</p>
<div><h1>{e(p["title"])}</h1><p class="lead">{e(p["theatre"])} · {lead}</p></div>
<div class="card">{f"<p>{e(p['desc'])}</p>" if p.get("desc") else ""}<dl class="facts">{"".join(f"<dt>{a}</dt><dd>{b}</dd>" for a, b in facts)}</dl>
{f'<p style="margin-top:10px"><a href="{e(p["page"])}" rel="noopener" target="_blank">Esityksen sivu teatterin sivustolla ↗</a></p>' if p.get("page") else ""}</div>
{listing}"""
        ld = [crumbs_ld([("Teatterit", "/teatterit/"), (p["theatre"], tp), (p["title"], path)])]
        ld += [event_ld(s, p) for s in p["_shows"]]
        write(path, page(path, f"{p['title']} – {p['theatre']}: liput ja näytökset | Lippu illaksi", desc, body, jsonld=ld))
        urls.append((path, today.isoformat() if p["_shows"] else v["last"], "0.8" if p["_shows"] else "0.3"))

    # 404, sitemap, robots
    write("404.html", page("/404.html", "Sivua ei löytynyt | Lippu illaksi", "Sivua ei löytynyt.",
                           '<div><h1>Sivua ei löytynyt</h1><p class="lead">Esitys on ehkä poistunut ohjelmistosta. '
                           'Katso <a href="/">lähipäivien näytökset</a> tai <a href="/teatterit/">teatterit</a>.</p></div>', noindex=True))
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u, mod, prio in urls:
            f.write(f"<url><loc>{e(BASE + u)}</loc><lastmod>{mod}</lastmod><priority>{prio}</priority></url>\n")
        f.write("</urlset>\n")
    with open("robots.txt", "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)
    print(f"SEO-sivut valmiit: {len(urls)} osoitetta ({len(active)} esitystä näytöksineen, {len(state) - len(active)} arkistossa).")


if __name__ == "__main__":
    main()
