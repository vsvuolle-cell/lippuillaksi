"""Lippu illaksi – päivittää data.json-tiedoston joka aamu.

Hakee teattereiden ohjelmistosivut, poimii lähipäivien näytökset Clauden avulla
ja kirjoittaa ne data.json-tiedostoon, jonka sivusto lukee.
Ajetaan GitHub Actionsissa (.github/workflows/update.yml).
"""
import datetime as dt
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
    {"name": "Helsingin Kaupunginteatteri", "city": "Helsinki",
     "start": ["https://hkt.fi/esitykset/"],
     "follow": r"^https://hkt\.fi/esitykset/[^/?#]+/?$",
     "hint": "Näyttämöitä ovat mm. Suuri näyttämö, Pieni näyttämö, Arena-näyttämö, Studio Pasila ja Lilla Teatern."},
    {"name": "Svenska Teatern", "city": "Helsinki",
     "start": ["https://svenskateatern.fi/repertoar/"],
     "follow": r"^https://svenskateatern\.fi/repertoar/[^/?#]+/?$",
     "hint": "Ruotsinkielinen teatteri. 'Platser finns'=many, 'Ett fåtal platser kvar'=few, 'Fullbokat'=sold."},
    {"name": "Ryhmäteatteri", "city": "Helsinki",
     "start": ["https://www.ryhmateatteri.fi/ohjelmisto-liput/"],
     "follow": r"^https://www\.ryhmateatteri\.fi/ohjelma/[^/?#]+/?$", "hint": ""},
    {"name": "KOM-teatteri", "city": "Helsinki",
     "start": ["https://kom-teatteri.fi/ohjelmisto/"],
     "follow": r"^https://kom-teatteri\.fi/ohjelmisto/[^/?#]+/?$", "hint": ""},
    {"name": "Q-teatteri", "city": "Helsinki",
     "start": ["https://www.q-teatteri.fi/esitykset"],
     "follow": r"^https://www\.q-teatteri\.fi/esitykset/[^/?#]+/?$",
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
     "hint": "Jätä pois konsertit."},
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

    productions, showings, theatres = {}, [], []
    for th in THEATRES:
        print(f"\n== {th['name']}")
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
    seen, uniq = set(), []
    for r in sorted(showings, key=lambda r: (r["date"], r["time"], r["p"])):
        k = (r["p"], r["date"], r["time"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)

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
