"""Lippu illaksi – päivittää data.json-tiedoston joka aamu.

Hakee teattereiden ohjelmistosivut, poimii lähipäivien näytökset Clauden avulla
ja kirjoittaa ne data.json-tiedostoon, jonka sivusto lukee.
Ajetaan GitHub Actionsissa (.github/workflows/update.yml).
"""
import datetime as dt
import html as html_lib
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urldefrag
from zoneinfo import ZoneInfo

import anthropic
import requests
from bs4 import BeautifulSoup

MODEL = os.environ.get("MODEL", "claude-haiku-4-5")
DAYS_AHEAD = 7
MAX_FOLLOW = 30          # montako esityssivua per teatteri enintään
MAX_CHARS = 18000        # sivutekstin enimmäispituus Claudelle
TZ = ZoneInfo("Europe/Helsinki")
UA = "Mozilla/5.0 (compatible; LippuIllaksi/1.0; +https://lippuillaksi.fi)"
GENRES = ["Draama", "Komedia", "Musikaali", "Ooppera ja tanssi", "Lapsille"]

# Teatterit: aloitussivu(t) ja säännöllinen lauseke esityssivujen linkeille.
THEATRES = [
    {"name": "Suomen Kansallisteatteri", "city": "Helsinki",
     "start": ["https://www.kansallisteatteri.fi/ohjelmisto/esitykset-a-o"],
     "follow": r"^https://www\.kansallisteatteri\.fi/esitys/[^/?#]+$",
     "hint": "Saatavuus: 'Paikkoja vapaana'=many, 'Muutama jäljellä'=few, 'Loppuunmyyty'=sold."},
    {"name": "Helsingin Kaupunginteatteri", "city": "Helsinki", "api": "hkt"},
    {"name": "Svenska Teatern", "city": "Helsinki", "api": "svenska"},
    {"name": "Ryhmäteatteri", "city": "Helsinki",
     "start": ["https://www.ryhmateatteri.fi/ohjelmisto-liput/"],
     "follow": r"^https://www\.ryhmateatteri\.fi/ohjelma/[^/?#]+/?$", "hint": ""},
    {"name": "KOM-teatteri", "city": "Helsinki",
     "start": ["https://kom-teatteri.fi/ohjelmisto/"],
     "follow": r"^https://kom-teatteri\.fi/ohjelmisto/[^/?#]+/?$", "hint": ""},
    {"name": "Q-teatteri", "city": "Helsinki",
     "start": ["https://www.q-teatteri.fi/esitykset"],
     "follow": r"^https://(www\.)?q-teatteri\.fi/(fi/)?esitykset/[^/?#]+/?$",
     "hint": "Jos sivulla lukee, että esitys on poistunut ohjelmistosta, älä palauta sen näytöksiä."},
    {"name": "Teatteri Jurkka", "city": "Helsinki",
     "start": ["https://www.jurkka.fi/kalenteri/"],
     "follow": r"^https://www\.jurkka\.fi/ohjelmisto/[^/?#]+/?$", "hint": ""},
    {"name": "Teatteri Avoimet Ovet", "city": "Helsinki",
     "start": ["https://www.avoimetovet.fi/"],
     "follow": r"^https://www\.avoimetovet\.fi/[a-z0-9-]+/?$", "hint": ""},
    {"name": "Aleksanterin teatteri", "city": "Helsinki",
     "start": ["https://www.aleksanterinteatteri.fi/"],
     "follow": r"^https://www\.aleksanterinteatteri\.fi/naytelma/[^/?#]+/?$", "hint": ""},
    {"name": "Zodiak – Uuden tanssin keskus", "city": "Helsinki",
     "start": ["https://www.zodiak.fi/fi/ohjelmisto"],
     "follow": r"^https://www\.zodiak\.fi/fi/ohjelmisto/[^/?#]+$",
     "hint": "Tanssiteoksia: genre 'Ooppera ja tanssi'. Jätä pois keskustelut ja työpajat."},
    {"name": "Suomen Komediateatteri", "city": "Helsinki",
     "start": ["https://suomenkomediateatteri.fi/esitykset"],
     "follow": r"^https://suomenkomediateatteri\.fi/esitykset/[^/?#]+/?$", "hint": ""},
    {"name": "Suomen Kansallisooppera ja -baletti", "city": "Helsinki",
     "start": ["https://oopperabaletti.fi/ohjelmisto/"],
     "follow": r"^https://oopperabaletti\.fi/ohjelmisto/[^/?#]+/?$",
     "hint": "Ooppera ja baletti: genre 'Ooppera ja tanssi'."},
    {"name": "Espoon teatteri", "city": "Espoo",
     "start": ["https://espoonteatteri.fi/ohjelmisto/"],
     "follow": r"^https://espoonteatteri\.fi/ohjelmisto/[^/?#]+/?$", "hint": ""},
    {"name": "Teatteri Vantaa", "city": "Vantaa",
     "start": ["https://teatterivantaa.fi/"],
     "follow": r"^https://teatterivantaa\.fi/portfolio/[^/?#]+/?$",
     "hint": "Jätä pois konsertit (esim. SilkkiJazz, Sir Elwood duo, bändit ja laulajien keikat). Käytä esityksen nimenä lyhyttä nimeä ilman esiintyjän nimeä edessä."},
]

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "fi,sv;q=0.8,en;q=0.5"})


def fetch(url):
    try:
        r = session.get(url, timeout=25)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            print(f"  ! {r.status_code} {url}")
            return None
        return r.text
    except requests.RequestException as e:
        print(f"  ! {type(e).__name__} {url}")
        return None


def fetch_json(url):
    try:
        r = session.get(url, timeout=25)
        if r.status_code != 200:
            print(f"  ! {r.status_code} {url}")
            return None
        return r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  ! {type(e).__name__} {url}")
        return None


def walk(obj):
    """Kaikki sisäkkäiset sanakirjat."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def parse_minutes(txt):
    if not txt:
        return None
    h = re.search(r"(\d+)\s*(?:t|h)\b", txt)
    m = re.search(r"(\d+)\s*min", txt)
    total = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return total or None


def parse_price(txt):
    m = re.search(r"(\d+(?:[,.]\d+)?)\s*€", str(txt or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def api_hkt(days):
    """HKT:n oma rajapinta: näytökset, saatavuus ja paikkamäärä."""
    start = days[0].isoformat()
    items = {}
    for url in (f"https://hkt.fi/wp-json/tickets/date/{start}/{len(days)}",
                f"https://hkt.fi/wp-json/tickets/lillan-date/{start}/{len(days)}"):
        data = fetch_json(url)
        for d in walk(data):
            if "timestamp" in d and d.get("title"):
                items[d.get("id") or (d["title"], d["timestamp"])] = d
    rows = []
    for d in items.values():
        when = dt.datetime.fromtimestamp(int(d["timestamp"]), TZ)
        dm = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", str(d.get("date", "")).strip())
        tm = re.fullmatch(r"(\d{1,2})[:.](\d{2})", str(d.get("time", "")).strip())
        if dm and tm:  # sivuston omat päivä- ja aikakentät ovat luotettavampia kuin aikaleima
            when = dt.datetime(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)), int(tm.group(1)), int(tm.group(2)))
        av = str(d.get("availability", "")).lower()
        if str(d.get("event_is_manually_sold_out")) == "1" or av in ("soldout", "sold_out", "sold", "none", "unavailable"):
            avail = "sold"
        elif av in ("limited", "few", "low"):
            avail = "few"
        elif av in ("available", "good", "high", "plenty"):
            avail = "many"
        else:
            avail = "unknown"
        seats = int(d["seats_avail"]) if str(d.get("seats_avail", "")).isdigit() and avail == "few" else None
        venue = str(d.get("venue") or "").replace("LÄMPIÓ", "lämpiö").strip()
        if "lilla" in venue.lower() or d.get("language") == "sv":
            venue = venue or "Lilla Teatern"
        dur_txt = d.get("duration") or ""
        rows.append({"title": d["title"].strip(), "stage": venue or None, "date": when.date().isoformat(),
                     "time": when.strftime("%H.%M"), "dur": parse_minutes(dur_txt),
                     "inter": True if re.search(r"väliaj|väliai", dur_txt.lower()) else None,
                     "lang": {"fi": "suomi", "sv": "ruotsi", "en": "englanti"}.get(d.get("language")),
                     "price": parse_price(d.get("prices")), "avail": avail, "seats": seats,
                     "url": d.get("url"), "page": d.get("link") or d.get("permalink"),
                     "context": " ".join(str(d.get(k) or "") for k in ("title_over", "title_under", "caption"))})
    return rows


def api_svenska(days):
    """Svenska Teaternin WordPress-rajapinta."""
    days_iso = {d.isoformat() for d in days}
    status = {0: "many", 1: "few", 2: "sold"}
    rows = []
    for page in (1, 2, 3):
        data = fetch_json(f"https://svenskateatern.fi/wp-json/wp/v2/shows?per_page=100&page={page}")
        if not data:
            break
        links = {item.get("id"): item.get("link") for item in data if isinstance(item, dict)}
        perfs = [d for d in walk(data) if "performance_id" in d]
        strings = []
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, str) and "performance_id" in x:
                strings.append(html_lib.unescape(x))
        for txt in strings:
            for m in re.finditer(r'\{[^{}]*"performance_id"[^{}]*\}', txt):
                try:
                    perfs.append(json.loads(m.group(0)))
                except json.JSONDecodeError:
                    pass
        for p in perfs:
            if p.get("date") not in days_iso or p.get("ticket_status") == 3:
                continue
            t = str(p.get("time", "")).zfill(4)
            rows.append({"title": str(p.get("title", "")).strip(), "stage": p.get("venue"), "date": p["date"],
                         "time": f"{t[:2]}.{t[2:]}", "avail": status.get(p.get("ticket_status"), "unknown"),
                         "url": p.get("tickets_link"), "lang": "ruotsi",
                         "page": links.get(p.get("production_id"))})
        if len(data) < 100:
            break
    return rows


DESCRIBE = """Alla on tietoa esityksestä "{title}" ({theatre}). Palauta VAIN JSON-objekti:
{{"genre": yksi näistä {genres}, "desc": 1–2 virkettä suomeksi OMIN SANOIN siitä, mistä esityksessä on kyse,
 "dur": kesto minuutteina väliaikoineen tai null, "inter": true/false/null, "lang": esityskieli suomeksi tai null,
 "subs": tekstitys lyhyesti tai null, "price": halvin aikuisten lipun hinta euroina tai null,
 "is_performance": false jos kyse on konsertista, keskustelusta tai muusta kuin esityksestä, muuten true}}
Älä keksi tietoja, joita tekstissä ei ole (käytä null)."""


def describe(client, theatre, title, text):
    prompt = DESCRIBE.format(title=title, theatre=theatre, genres=", ".join(f'"{g}"' for g in GENRES))
    try:
        msg = client.messages.create(model=MODEL, max_tokens=800, messages=[
            {"role": "user", "content": prompt + "\n\n--- TEKSTI ---\n" + (text or title)[:8000]}])
        out = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", out, re.S)
        return json.loads(m.group(0)) if m else {}
    except (anthropic.APIError, json.JSONDecodeError) as e:
        print(f"  ! kuvaus epäonnistui ({type(e).__name__})")
        return {}


def page_text(html, base):
    """Sivun teksti ja linkit tiiviissä muodossa."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "header", "footer", "nav", "form"]):
        tag.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(base, a["href"]))[0]
        if href.startswith("http"):
            links.append(href)
            a.replace_with(f"{a.get_text(' ', strip=True)} [{href}]")
    text = re.sub(r"[ \t\r\f\v]+", " ", soup.get_text("\n"))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, links


def date_tokens(days):
    toks = set()
    for d in days:
        toks |= {f"{d.day}.{d.month}.", f"{d.day:02d}.{d.month:02d}.", d.isoformat(), f"{d.day}/{d.month}"}
    return toks


def mentions_dates(text, toks):
    return any(re.search(r"(?<![\d.])" + re.escape(t), text) for t in toks)


PROMPT = """Olet tarkka tiedonpoimija. Alla on teatterin verkkosivun teksti (linkit hakasulkeissa).
Teatteri: {theatre} ({city}). Sivu: {url}
Poimi KAIKKI tämän teatterin yksittäiset näytökset päivämäärillä {first} – {last} (vuosi {year}).
{hint}

Palauta VAIN JSON-taulukko, jossa jokainen näytös on objekti:
{{"title": esityksen nimi, "stage": näyttämö tai null, "date": "YYYY-MM-DD", "time": "HH.MM",
 "genre": yksi näistä {genres}, "dur": kesto minuutteina väliaikoineen tai null, "inter": true/false/null (väliaika),
 "lang": esityskieli suomeksi (esim. "suomi", "ruotsi", "ei puhetta") tai null, "subs": tekstitys lyhyesti tai null,
 "price": halvin aikuisten peruslipun hinta euroina numerona tai null,
 "avail": "many" | "few" | "sold" | "unknown", "url": suora ostolinkki tälle näytökselle (tai esityssivu),
 "page": esityssivun osoite, "desc": 1–2 virkettä suomeksi OMIN SANOIN siitä, mistä esityksessä on kyse,
 "note": esim. "Ensi-ilta" tai "Ennakkonäytös" tai null}}

Säännöt:
- Älä keksi mitään. Jos päivää tai kellonaikaa ei lue sivulla, jätä näytös pois.
- avail: käytä many/few/sold vain, jos sivu kertoo sen kyseisestä näytöksestä. Muuten "unknown".
- Jätä pois konsertit, keskustelut, työpajat, opastetut kierrokset ja peruutetut näytökset.
- Jos sivulla ei ole näytöksiä annetulla aikavälillä, palauta [].
"""


def extract(client, th, url, text, days):
    prompt = PROMPT.format(theatre=th["name"], city=th["city"], url=url, first=days[0].isoformat(),
                           last=days[-1].isoformat(), year=days[0].year, hint=th.get("hint", ""),
                           genres=", ".join(f'"{g}"' for g in GENRES))
    for attempt in range(3):
        try:
            msg = client.messages.create(model=MODEL, max_tokens=4000, messages=[
                {"role": "user", "content": prompt + "\n\n--- SIVUN TEKSTI ---\n" + text[:MAX_CHARS]}])
            out = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            m = re.search(r"\[.*\]", out, re.S)
            return json.loads(m.group(0)) if m else []
        except (anthropic.APIError, json.JSONDecodeError) as e:
            print(f"  ! Claude-virhe ({type(e).__name__}), yritys {attempt + 1}")
            time.sleep(5 * (attempt + 1))
    return []


def slug(s):
    s = s.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("å", "a"), ("é", "e")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60]


def valid(s, days_iso):
    return (isinstance(s, dict) and s.get("title") and s.get("date") in days_iso
            and re.fullmatch(r"\d{1,2}[.:]\d{2}", str(s.get("time", ""))))


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY puuttuu (lisää se säilön Settings → Secrets → Actions).")
    client = anthropic.Anthropic()
    now = dt.datetime.now(TZ)
    days = [now.date() + dt.timedelta(days=i) for i in range(DAYS_AHEAD)]
    days_iso = {d.isoformat() for d in days}
    toks = date_tokens(days)

    try:
        old = json.load(open("data.json", encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = {"productions": {}, "showings": []}
    old_desc = {(p["theatre"], p["title"]): p.get("desc") for p in old.get("productions", {}).values()}

    old_prods = {(p["theatre"], p["title"]): p for p in old.get("productions", {}).values()}
    productions, showings, theatres = {}, [], []
    for th in THEATRES:
        print(f"\n== {th['name']}")
        if th.get("api"):
            rows = api_hkt(days) if th["api"] == "hkt" else api_svenska(days)
            found, page_cache = 0, {}
            for s in rows:
                if not valid(s, days_iso):
                    continue
                pid = slug(th["name"])[:20] + "-" + slug(s["title"])
                if pid not in productions:
                    prev = old_prods.get((th["name"], s["title"]))
                    if prev:
                        meta = dict(prev)
                    else:
                        text = s.get("context") or ""
                        if s.get("page"):
                            if s["page"] not in page_cache:
                                h = fetch(s["page"])
                                page_cache[s["page"]] = page_text(h, s["page"])[0] if h else ""
                            text = page_cache[s["page"]] + "\n" + text
                        meta = describe(client, th["name"], s["title"], text)
                        if meta.get("is_performance") is False:
                            print(f"  - ohitetaan (ei esitys): {s['title']}")
                            productions[pid] = None
                            continue
                    productions[pid] = {
                        "theatre": th["name"], "city": th["city"], "stage": s.get("stage") or meta.get("stage") or th["name"],
                        "title": s["title"], "genre": meta.get("genre") if meta.get("genre") in GENRES else "Draama",
                        "dur": s.get("dur") or meta.get("dur"), "inter": s.get("inter") if s.get("inter") is not None else meta.get("inter"),
                        "lang": s.get("lang") or meta.get("lang"), "subs": meta.get("subs"),
                        "page": s.get("page") or meta.get("page") or "", "desc": meta.get("desc") or "",
                        "_price": meta.get("price") if isinstance(meta.get("price"), (int, float)) else None,
                    }
                if productions[pid] is None:
                    continue
                row = {"p": pid, "date": s["date"], "time": s["time"],
                       "price": s.get("price") if isinstance(s.get("price"), (int, float)) else productions[pid].get("_price"),
                       "avail": s.get("avail", "unknown"), "url": s.get("url") or productions[pid]["page"]}
                if s.get("seats"):
                    row["seats"] = s["seats"]
                showings.append(row)
                found += 1
            status = "ok" if found else ("none" if rows is not None else "fail")
            theatres.append({"name": th["name"], "city": th["city"], "status": status,
                             **({} if found else {"note": "Ei näytöksiä tai tietoja ei saatu haettua"})})
            print(f"  => {found} näytöstä ({status})")
            continue
        pages, fetched_any = {}, False
        for url in th["start"]:
            html = fetch(url)
            if not html:
                continue
            fetched_any = True
            text, links = page_text(html, url)
            pages[url] = text
            follow = re.compile(th["follow"])
            for link in dict.fromkeys(l for l in links if follow.match(l) and l not in th["start"]):
                if len(pages) > MAX_FOLLOW:
                    break
                h = fetch(link)
                if h:
                    pages[link] = page_text(h, link)[0]
                time.sleep(0.5)
        found = 0
        for url, text in pages.items():
            if not mentions_dates(text, toks):
                continue
            print(f"  - {url}")
            for s in extract(client, th, url, text, days):
                if not valid(s, days_iso):
                    continue
                pid = slug(th["name"])[:20] + "-" + slug(s["title"])
                if pid not in productions:
                    productions[pid] = {
                        "theatre": th["name"], "city": th["city"], "stage": s.get("stage") or th["name"],
                        "title": s["title"], "genre": s.get("genre") if s.get("genre") in GENRES else "Draama",
                        "dur": s.get("dur"), "inter": s.get("inter"), "lang": s.get("lang"), "subs": s.get("subs"),
                        "page": s.get("page") or url,
                        "desc": old_desc.get((th["name"], s["title"])) or s.get("desc") or "",
                    }
                t = str(s["time"]).replace(":", ".")
                t = t if len(t.split(".")[0]) == 2 else "0" + t
                row = {"p": pid, "date": s["date"], "time": t,
                       "price": s.get("price") if isinstance(s.get("price"), (int, float)) else None,
                       "avail": s.get("avail") if s.get("avail") in ("many", "few", "sold", "unknown") else "unknown",
                       "url": s.get("url") or productions[pid]["page"]}
                if s.get("note"):
                    row["note"] = s["note"]
                if s.get("subs") and s.get("subs") != productions[pid]["subs"]:
                    row["subs"] = s["subs"]
                showings.append(row)
                found += 1
        status = "ok" if found else ("none" if fetched_any else "fail")
        note = {"ok": None, "none": "Ei näytöksiä tai ajat eivät vielä haettavissa",
                "fail": "Sivustoa ei saatu haettua"}[status]
        theatres.append({"name": th["name"], "city": th["city"], "status": status, **({"note": note} if note else {})})
        print(f"  => {found} näytöstä ({status})")

    # poista tuplat
    productions = {k: {kk: vv for kk, vv in v.items() if kk != "_price"} for k, v in productions.items() if v}
    seen, uniq = set(), []
    for r in sorted(showings, key=lambda r: (r["date"], r["time"], r["p"])):
        if r["p"] not in productions:
            continue
        k = (productions[r["p"]]["theatre"], r["date"], r["time"], r["p"])
        k2 = (productions[r["p"]]["theatre"], productions[r["p"]]["stage"], r["date"], r["time"])
        if k not in seen and k2 not in seen:
            seen.update({k, k2})
            uniq.append(r)
    used = {r["p"] for r in uniq}
    productions = {k: v for k, v in productions.items() if k in used}

    # turvaraja: älä korvaa hyvää dataa selvästi epäonnistuneella ajolla
    if len(uniq) < 15:
        sys.exit(f"Vain {len(uniq)} näytöstä – data.json jätetään ennalleen.")

    data = {"updated": now.isoformat(timespec="minutes"), "from": days[0].isoformat(), "to": days[-1].isoformat(),
            "productions": productions, "showings": uniq, "theatres": theatres}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    print(f"\nValmis: {len(uniq)} näytöstä, {len(productions)} esitystä.")


if __name__ == "__main__":
    main()
