"""
Live play alerts for Knee-ver Over the Hill, sent to Discord.

Every run (GitHub Actions, every ~5 min; loops every 40s while games are live):
  - checks ESPN's public NFL data for touchdowns, 40+ yard plays and D/ST takeaways by your players
  - posts the alert to Discord right away, with a one-tap X search for that game
  - keeps watching for ESPN's clip of the play and posts the video into Discord when it appears

Roster comes from data/team.json (ESPN sync) or the default roster below.
"""
import json, os, re, sys, time, uuid, pathlib, datetime, urllib.request, urllib.parse

WEBHOOK = os.environ.get("DISCORD_WEBHOOK") or "https://discord.com/api/webhooks/1557042917086863378/6YmcA-UBA3B2Z8tYI2aWpsqUuH-Z2vgbpXtK29h2I4Yip2UTji-AmWYYZo-3bbfRSm34"
SITE_URL = os.environ.get("SITE_URL", "")
BIG_PLAY = 40            # yards
CLIP_WAIT_MIN = 120      # keep looking for a clip this long after the play
LOOP_SECONDS = 240       # how long one run keeps checking while games are live
LOOP_EVERY = 40

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "alerts-state.json"
TEAM = ROOT / "data" / "team.json"
XCLIPS = ROOT / "data" / "xclips.json"
X_NETWORKS = ["NFL", "NFLonFOX", "NFLonCBS", "NFLonPrime", "NFLonNBC", "ESPNNFL"]
X_SCAN_EVERY = 150        # seconds between X scans while games are on
SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUM = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event="
UA = {"User-Agent": "Mozilla/5.0 (KneeverAlerts)"}

DEFAULT_ROSTER = [
    ("QB", "Joe Burrow", "CIN", "QB"), ("RB", "Omarion Hampton", "LAC", "RB"), ("RB", "Bhayshul Tuten", "JAX", "RB"),
    ("WR", "Malik Nabers", "NYG", "WR"), ("WR", "Josh Downs", "IND", "WR"), ("FLEX", "Jaxon Smith-Njigba", "SEA", "WR"),
    ("FLEX", "Tetairoa McMillan", "CAR", "WR"), ("D/ST", "Bears", "CHI", "D/ST"), ("K", "Harrison Mevis", "LAR", "K"),
    ("Bench", "Mike Evans", "SF", "WR"), ("Bench", "Blake Corum", "LAR", "RB"), ("Bench", "Zach Charbonnet", "SEA", "RB"),
    ("Bench", "Dylan Sampson", "CLE", "RB"), ("Bench", "Devaughn Vele", "NO", "WR"), ("Bench", "George Holani", "SEA", "RB"),
    ("Bench", "Emanuel Wilson", "SEA", "RB"), ("IR", "Jonathan Brooks", "CAR", "RB"), ("IR", "Alec Pierce", "IND", "WR"),
]
TEAM_X = {"ARI": "AZCardinals", "ATL": "AtlantaFalcons", "BAL": "Ravens", "BUF": "BuffaloBills", "CAR": "Panthers",
          "CHI": "ChicagoBears", "CIN": "Bengals", "CLE": "Browns", "DAL": "dallascowboys", "DEN": "Broncos", "DET": "Lions",
          "GB": "packers", "HOU": "HoustonTexans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs", "LV": "Raiders",
          "LAC": "chargers", "LAR": "RamsNFL", "MIA": "MiamiDolphins", "MIN": "Vikings", "NE": "Patriots", "NO": "Saints",
          "NYG": "Giants", "NYJ": "nyjets", "PHI": "Eagles", "PIT": "steelers", "SF": "49ers", "SEA": "Seahawks",
          "TB": "Buccaneers", "TEN": "Titans", "WSH": "Commanders"}
NICK = {"CHI": "Bears", "CIN": "Bengals", "LAC": "Chargers", "JAX": "Jaguars", "NYG": "Giants", "IND": "Colts", "SEA": "Seahawks",
        "CAR": "Panthers", "LAR": "Rams", "SF": "49ers", "CLE": "Browns", "NO": "Saints"}


# ---------- helpers ----------
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return json.load(r)


def last_name(n):
    parts = re.sub(r"\s+(Jr|Sr|II|III|IV|V)\.?$", "", n, flags=re.I).split()
    return parts[-1] if parts else n


def mentions(text, name):
    return re.search(r"\b" + re.escape(last_name(name)) + r"\b", text or "", re.I) is not None


def roster():
    try:
        r = json.loads(TEAM.read_text()).get("roster") or []
        if r:
            return [{"slot": p["slot"], "name": p["name"], "team": p["team"], "pos": p["pos"]} for p in r]
    except Exception:
        pass
    return [{"slot": a, "name": b, "team": c, "pos": d} for a, b, c, d in DEFAULT_ROSTER]


def label(p):
    return f"{p['name']} D/ST" if p["pos"] == "D/ST" else p["name"]


def x_search(p, game_date, opp):
    accts = ["NFL", TEAM_X.get(p["team"]), TEAM_X.get(opp), "NFLonFOX", "NFLonCBS", "NFLonPrime", "NFLonNBC"]
    frm = "(" + " OR ".join("from:" + a for a in accts if a) + ")"
    who = '(sack OR interception OR pick OR fumble OR touchdown OR defense)' if p["pos"] == "D/ST" else f'"{last_name(p["name"])}"'
    d = datetime.datetime.fromisoformat(game_date.replace("Z", "+00:00")) - datetime.timedelta(hours=6)
    q = f"{who} {frm} filter:native_video since:{d.date()} until:{(d + datetime.timedelta(days=2)).date()}"
    return "https://x.com/search?" + urllib.parse.urlencode({"q": q, "f": "live"})


# ---------- Discord ----------
def discord(payload=None, file_bytes=None, filename=None):
    if not WEBHOOK.startswith("https://"):
        print("No Discord webhook set; would send:", json.dumps(payload)[:300])
        return
    payload = dict(payload or {}, username="Knee-ver Alerts")
    headers = {"User-Agent": "DiscordBot (https://github.com, 1.0)"}
    if file_bytes:
        b = "----kvh" + uuid.uuid4().hex
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n"
                .encode() + json.dumps(payload).encode() + b"\r\n" +
                f"--{b}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{filename}\"\r\nContent-Type: video/mp4\r\n\r\n"
                .encode() + file_bytes + b"\r\n" + f"--{b}--\r\n".encode())
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    else:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            urllib.request.urlopen(urllib.request.Request(WEBHOOK, data=body, headers=headers, method="POST"), timeout=60)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3)
                continue
            print("Discord error", e.code, e.read()[:200])
            return False
        except Exception as e:
            print("Discord error", e)
            return False


def send_clip(p, v):
    title = f"🎥 {v['headline']}"
    small = v.get("small")
    if small:
        try:
            with urllib.request.urlopen(urllib.request.Request(small, headers=UA), timeout=60) as r:
                size = int(r.headers.get("Content-Length") or 0)
                if 0 < size <= 9_500_000:
                    data = r.read()
                    if discord({"content": title}, data, f"{last_name(p['name']).lower()}.mp4"):
                        return
        except Exception as e:
            print("clip download failed", e)
    discord({"content": f"{title}\n{v['url']}"})


# ---------- ESPN parsing ----------
def videos(summary):
    out = []
    for v in summary.get("videos") or []:
        L = v.get("links") or {}
        src = L.get("source") or {}
        big = (src.get("HD") or {}).get("href") or src.get("href") or ((L.get("mobile") or {}).get("source") or {}).get("href")
        small = ((L.get("mobile") or {}).get("source") or {}).get("href") or src.get("href")
        if big:
            out.append({"id": str(v.get("id")), "headline": v.get("headline", ""), "text": f"{v.get('headline','')} {v.get('description','')}",
                        "url": big, "small": small})
    return out


def plays(summary):
    """Every play with its offense team, from drives (preferred) and scoring plays."""
    seen, out = set(), []
    drives = summary.get("drives") or {}
    dl = list(drives.get("previous") or [])
    if drives.get("current"):
        dl.append(drives["current"])
    for d in dl:
        off = ((d.get("team") or {}).get("abbreviation")) or ""
        for pl in d.get("plays") or []:
            pid = str(pl.get("id"))
            if pid in seen:
                continue
            seen.add(pid)
            out.append({"id": pid, "type": (pl.get("type") or {}).get("text", ""), "text": pl.get("text", ""), "off": off,
                        "yds": pl.get("statYardage") or 0, "q": (pl.get("period") or {}).get("number"),
                        "clock": (pl.get("clock") or {}).get("displayValue", ""), "score": bool(pl.get("scoringPlay")),
                        "scoreTeam": None})
    for sp in summary.get("scoringPlays") or []:
        pid = str(sp.get("id"))
        t = (sp.get("team") or {}).get("abbreviation", "")
        match = next((x for x in out if x["id"] == pid), None)
        if match:
            match.update(score=True, scoreTeam=t, type=(sp.get("type") or {}).get("text", match["type"]))
        else:
            out.append({"id": pid, "type": (sp.get("type") or {}).get("text", ""), "text": sp.get("text", ""), "off": t, "yds": 0,
                        "q": (sp.get("period") or {}).get("number"), "clock": (sp.get("clock") or {}).get("displayValue", ""),
                        "score": True, "scoreTeam": t})
    return out


def classify(p, pl, opp):
    """Returns an alert kind for this player on this play, or None."""
    t, txt = pl["type"], pl["text"]
    if re.search(r"no play|penalty", txt, re.I) and not pl["score"]:
        return None
    if p["pos"] == "D/ST":
        if pl["score"] and pl.get("scoreTeam") == p["team"] and re.search(r"interception|fumble|kickoff return|punt return|blocked|safety", t, re.I):
            return "D/ST touchdown" if "touchdown" in t.lower() else "D/ST score"
        if pl["off"] == opp and re.search(r"interception", t, re.I):
            return "Interception"
        if pl["off"] == opp and re.search(r"fumble recovery \(opponent\)", t, re.I):
            return "Fumble recovery"
        return None
    if p["pos"] == "K" or not mentions(txt, p["name"]):
        return None
    if pl["score"] and "touchdown" in t.lower() and (pl.get("scoreTeam") in (None, p["team"])):
        return "Touchdown"
    if pl["off"] == p["team"] and pl["yds"] >= BIG_PLAY and re.search(r"rush|pass|reception", t, re.I):
        return f"{pl['yds']}-yard play"
    return None


# ---------- X (official accounts) ----------
def x_timeline(handle):
    """Recent posts from a public X account via X's embed-timeline feed (no login). Best effort."""
    req = urllib.request.Request(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}",
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                          "(KHTML, like Gecko) Chrome/128.0 Safari/537.36", "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=25) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return []
    entries = (((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("timeline") or {}).get("entries") or []
    out = []
    for e in entries:
        t = (e.get("content") or {}).get("tweet") or {}
        media = ((t.get("extended_entities") or {}).get("media")) or ((t.get("entities") or {}).get("media")) or []
        if not t.get("id_str") or not any(mm.get("type") == "video" for mm in media):
            continue
        try:
            dt = datetime.datetime.strptime(t.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y")
        except Exception:
            continue
        out.append({"id": t["id_str"], "text": t.get("full_text") or t.get("text") or "",
                    "user": (t.get("user") or {}).get("screen_name") or handle, "date": dt.isoformat()})
    return out


def x_matches(p, clip):
    text, user = clip["text"], clip["user"].lower()
    team_acct = (TEAM_X.get(p["team"]) or "").lower()
    if p["pos"] == "D/ST":
        return user == team_acct and re.search(r"defen|sack|intercept|pick|fumble|strip|takeaway|return|block|safety", text, re.I)
    full = r"\b" + r"[\s\-]+".join(re.escape(w) for w in p["name"].split()) + r"\b"
    if re.search(full, text, re.I):
        return True
    return user == team_acct and mentions(text, p["name"])


def scan_x(state, ros, active_teams):
    if not active_teams:
        return
    handles = list(X_NETWORKS) + [TEAM_X[t] for t in sorted(active_teams) if t in TEAM_X]
    try:
        store = json.loads(XCLIPS.read_text())
    except Exception:
        store = {"clips": []}
    have = {(c["id"], c["player"]) for c in store["clips"]}
    first = not state.get("xInit")
    now = datetime.datetime.now(datetime.timezone.utc)
    found, failures = 0, 0
    for h in handles:
        try:
            posts = x_timeline(h)
        except Exception as e:
            failures += 1
            print(f"X feed {h} failed: {e}")
            continue
        for c in posts:
            for p in ros:
                if p["team"] not in active_teams or not x_matches(p, c) or (c["id"], p["name"]) in have:
                    continue
                have.add((c["id"], p["name"]))
                store["clips"].append({"id": c["id"], "user": c["user"], "date": c["date"], "player": p["name"],
                                       "team": p["team"], "text": c["text"][:280]})
                found += 1
                age = (now - datetime.datetime.fromisoformat(c["date"])).total_seconds()
                if not first and age < 3 * 3600:
                    discord({"content": f"🎥 **{label(p)}** on X\nhttps://fixupx.com/{c['user']}/status/{c['id']}"})
                    state["pending"] = [x for x in state["pending"] if x["name"] != p["name"]]
    print(f"X scan: {len(handles)} accounts, {failures} failed, {found} new clips")
    state["xInit"] = True
    store["clips"] = sorted(store["clips"], key=lambda c: c["date"])[-800:]
    XCLIPS.write_text(json.dumps(store, indent=0))


# ---------- main ----------
def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return None


def run_once(state, ros, first_run):
    sb = get(SB)
    teams = {p["team"] for p in ros}
    live = False
    now = time.time()
    state["_active"] = set()
    for ev in sb.get("events") or []:
        c = ev["competitions"][0]
        st = c["status"]["type"]["state"]
        comp = {x["homeAway"]: x for x in c["competitors"]}
        h, a = comp["home"]["team"]["abbreviation"], comp["away"]["team"]["abbreviation"]
        if not ({h, a} & teams) or st == "pre":
            continue
        live = live or st == "in"
        try:
            age_h = (datetime.datetime.now(datetime.timezone.utc) -
                     datetime.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception:
            age_h = 0
        if st == "in" or age_h < 30:
            state["_active"].update({h, a} & teams)
        recent_final = st == "post" and any(pd["event"] == ev["id"] for pd in state["pending"])
        if st == "post" and not recent_final and ev["id"] in state.get("doneEvents", []):
            continue
        summ = get(SUM + ev["id"])
        score = f"{a} {comp['away'].get('score','0')}, {h} {comp['home'].get('score','0')}"
        vids = videos(summ)

        for pl in plays(summ):
            for p in ros:
                if p["team"] not in (h, a):
                    continue
                opp = a if p["team"] == h else h
                kind = classify(p, pl, opp)
                if not kind:
                    continue
                key = f"{pl['id']}|{p['name']}"
                if key in state["seen"]:
                    continue
                state["seen"].append(key)
                if first_run:
                    continue
                bench = " (bench)" if p["slot"] in ("Bench", "IR") else ""
                embed = {
                    "title": f"{'🏈' if 'ouchdown' in kind else '⚡'} {kind}: {label(p)}{bench}",
                    "description": pl["text"],
                    "color": 0xFF5A1F if "ouchdown" in kind else 0x1F5136,
                    "footer": {"text": f"Q{pl['q']} {pl['clock']}  |  {score}"},
                    "fields": [{"name": "Clip", "value": f"[Find it on X]({x_search(p, ev['date'], opp)})"
                                + (f"  |  [Open tracker]({SITE_URL})" if SITE_URL else "") +
                                "\nESPN's video will post here when it's up.", "inline": False}],
                }
                discord({"embeds": [embed]})
                state["pending"].append({"event": ev["id"], "name": p["name"], "team": p["team"], "pos": p["pos"],
                                         "slot": p["slot"], "at": now})

        # match clips to pending plays
        for pd in [x for x in state["pending"] if x["event"] == ev["id"]]:
            p = pd
            for v in vids:
                if v["id"] in state["sentClips"]:
                    continue
                ok = (re.search(NICK.get(p["team"], p["team"]), v["text"], re.I) and
                      re.search(r"defen|sack|intercept|pick|fumble|return|block|safety|strip", v["text"], re.I)) \
                    if p["pos"] == "D/ST" else mentions(v["text"], p["name"])
                if ok:
                    send_clip(p, v)
                    state["sentClips"].append(v["id"])
                    state["pending"].remove(pd)
                    break
        if st == "post" and not any(x["event"] == ev["id"] for x in state["pending"]):
            state.setdefault("doneEvents", []).append(ev["id"])

    state["pending"] = [x for x in state["pending"] if now - x["at"] < CLIP_WAIT_MIN * 60]
    return live


def main():
    state = load_state()
    first_run = state is None
    state = state or {"seen": [], "sentClips": [], "pending": [], "doneEvents": []}
    ros = roster()
    if first_run:
        discord({"embeds": [{"title": "✅ Knee-ver Alerts connected",
                             "description": f"Watching {len(ros)} players. Touchdowns, 40+ yard plays and D/ST takeaways will post here, then ESPN's clip when it's up."
                                            + (f"\n[Open tracker]({SITE_URL})" if SITE_URL else ""),
                             "color": 0x1F5136}]})
    start = time.time()
    while True:
        try:
            live = run_once(state, ros, first_run)
        except Exception as e:
            print("check failed:", e)
            live = False
        first_run = False
        if time.time() - state.get("xLast", 0) >= X_SCAN_EVERY:
            try:
                scan_x(state, ros, state.get("_active") or set())
            except Exception as e:
                print("X scan failed:", e)
            state["xLast"] = time.time()
        state.pop("_active", None)
        state["seen"] = state["seen"][-4000:]
        state["sentClips"] = state["sentClips"][-1000:]
        state["doneEvents"] = state.get("doneEvents", [])[-300:]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
        if not (live or state["pending"]) or time.time() - start > LOOP_SECONDS:
            break
        time.sleep(LOOP_EVERY)


if __name__ == "__main__":
    main()
