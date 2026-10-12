"""
Highlights and interviews for every player on Knee-ver Over the Hill.

Searches YouTube (no API key) for this week's highlight clips and interviews /
press conferences for each rostered player, keeps the ones that really name him,
and writes data/player-feed.json for the Clips tab on the site.

Runs from GitHub Actions. Skips itself if the feed was refreshed recently.
"""
import json, os, re, sys, time, pathlib, datetime, urllib.request, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEAM = ROOT / "data" / "team.json"
OUT = ROOT / "data" / "player-feed.json"
KEEP_DAYS = 10
MIN_AGE = int(os.environ.get("FEED_MIN_AGE", "3600"))   # seconds between refreshes

NICK = {"ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears", "CIN": "Bengals",
        "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers", "HOU": "Texans", "IND": "Colts",
        "JAX": "Jaguars", "KC": "Chiefs", "LV": "Raiders", "LAC": "Chargers", "LAR": "Rams", "MIA": "Dolphins", "MIN": "Vikings",
        "NE": "Patriots", "NO": "Saints", "NYG": "Giants", "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SF": "49ers",
        "SEA": "Seahawks", "TB": "Buccaneers", "TEN": "Titans", "WSH": "Commanders"}

INTERVIEW_RE = re.compile(r"interview|press conference|presser|post-?game|pre-?game|media availability|speaks|talks|"
                          r"reacts|on (?:the )?win|on (?:the )?loss|mic'?d up|sits down|1[- ]on[- ]1|locker room|podcast|joins|"
                          r"says|explains|discusses|breaks down", re.I)
JUNK_RE = re.compile(r"fantasy|start or sit|start/sit|sit or start|waiver|dynasty|rankings?|madden|mock draft|"
                     r"predictions?|betting|odds|props|parlay|trade value|buy or sell|sleepers?|busts?|nfl draft|"
                     r"college|high school|\bhs\b|recruit|reaction video|tier list", re.I)
DST_RE = re.compile(r"defen[cs]e|sack|interception|pick[- ]six|pick 6|takeaway|forced fumble|strip|turnover|goal[- ]line stand", re.I)
OFFICIAL_RE = re.compile(r"^(NFL|ESPN|NFL on (FOX|CBS|NBC|Prime)|CBS Sports|FOX Sports|NBC Sports|Prime Video|NFL Network|"
                         r"Bleacher Report|The Pat McAfee Show|Good Morning Football|SportsCenter|Sunday Night Football)", re.I)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+cb; SOCS=CAI"}
THIS_WEEK = "EgIIAw%3D%3D"   # YouTube filter: uploaded this week, videos only


def roster():
    try:
        r = json.loads(TEAM.read_text()).get("roster") or []
        if r:
            return [{"slot": p["slot"], "name": p["name"], "team": p["team"], "pos": p["pos"]} for p in r]
    except Exception:
        pass
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from live_alerts import roster as alerts_roster
        return alerts_roster()
    except Exception:
        return []


def last_name(n):
    parts = re.sub(r"\s+(Jr|Sr|II|III|IV|V)\.?$", "", n, flags=re.I).split()
    return parts[-1] if parts else n


def name_re(n):
    words = re.sub(r"\s+(Jr|Sr|II|III|IV|V)\.?$", "", n, flags=re.I).split()
    return re.compile(r"\b" + r"[\s\-.']*".join(re.escape(w) for w in words) + r"\b", re.I)


def yt_search(q):
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(q)}&sp={THIS_WEEK}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=25) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S) or \
        re.search(r'window\["ytInitialData"\]\s*=\s*(\{.*?\});', html, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o and isinstance(o["videoRenderer"], dict):
                out.append(o["videoRenderer"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def txt(x):
    if not x:
        return ""
    if "simpleText" in x:
        return x["simpleText"]
    return "".join(r.get("text", "") for r in x.get("runs") or [])


def rel_time(s, now):
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)", s or "")
    if not m:
        return now
    n, u = int(m.group(1)), m.group(2)
    secs = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}[u]
    return now - n * secs


def dur_secs(s):
    parts = [int(x) for x in re.findall(r"\d+", s or "")]
    t = 0
    for p in parts:
        t = t * 60 + p
    return t


def views_num(s):
    m = re.search(r"([\d,.]+)\s*([KMB]?)", (s or "").replace(",", ""), re.I)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return int(v * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def queries(p):
    nick = NICK.get(p["team"], p["team"])
    if p["pos"] == "D/ST":
        return [(f"{nick} defense highlights", "hl"), (f"{nick} defensive coordinator press conference", "int")]
    return [(f'"{p["name"]}" highlights', "hl"), (f'"{p["name"]}" interview press conference', "int")]


def matches(p, title):
    if JUNK_RE.search(title):
        return False
    nick = NICK.get(p["team"], p["team"])
    if p["pos"] == "D/ST":
        return re.search(r"\b" + re.escape(nick) + r"\b", title, re.I) and (DST_RE.search(title) or INTERVIEW_RE.search(title))
    if name_re(p["name"]).search(title):
        return True
    return bool(re.search(r"\b" + re.escape(last_name(p["name"])) + r"\b", title, re.I) and re.search(re.escape(nick), title, re.I))


def update(force=False):
    now = time.time()
    old = {}
    try:
        old = json.loads(OUT.read_text())
    except Exception:
        pass
    if not force and now - (old.get("ts") or 0) < MIN_AGE:
        print("player feed is fresh; skipping")
        return False
    ros = roster()
    if not ros:
        print("no roster; skipping")
        return False
    items = {i["id"]: i for i in old.get("items") or [] if now - (i.get("ts") or 0) < KEEP_DAYS * 86400}
    ok = 0
    for p in ros:
        for q, qkind in queries(p):
            try:
                vids = yt_search(q)
                ok += 1
            except Exception as e:
                print("search failed:", q, e)
                continue
            for v in vids[:20]:
                vid = v.get("videoId")
                title = txt(v.get("title"))
                if not vid or not title or not matches(p, title):
                    continue
                if v.get("badges") and any("LIVE" in json.dumps(b) for b in v["badges"]):
                    continue
                d = dur_secs(txt(v.get("lengthText")))
                if d and d > 60 * 60:      # skip full games / long shows
                    continue
                chan = txt(v.get("ownerText")) or txt(v.get("longBylineText"))
                kind = "int" if INTERVIEW_RE.search(title) else "hl"
                iid = "yt_" + vid
                ts = rel_time(txt(v.get("publishedTimeText")), now)
                prev = items.get(iid)
                players = sorted(set((prev or {}).get("players", [])) | {f'{p["name"]}|{p["team"]}'})
                items[iid] = {"id": iid, "vid": vid, "title": title, "channel": chan, "kind": kind, "players": players,
                              "ts": min(ts, (prev or {}).get("ts", ts)), "dur": d,
                              "views": views_num(txt(v.get("viewCountText"))),
                              "official": bool(OFFICIAL_RE.search(chan or "")) or
                                          bool(re.search(re.escape(NICK.get(p["team"], p["team"])), chan or "", re.I))}
            time.sleep(1.2)
    if not ok:
        print("YouTube searches all failed; keeping old feed")
        return False
    lst = sorted(items.values(), key=lambda i: -i["ts"])[:400]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"ts": now, "updated": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
                               "items": lst}, indent=0))
    print(f"player feed: {len(lst)} clips from {ok} searches")
    return True


if __name__ == "__main__":
    update(force=os.environ.get("FEED_FORCE") == "1")
