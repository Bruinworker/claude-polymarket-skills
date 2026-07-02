#!/usr/bin/env python3
"""
poly-state-scan — Arrow-Debreu state-space consistency scanner for Polymarket
soccer games.

Pulls every market of one game (via gamma game_id), reads the exact-score grid
as the state space (state prices = YES prices), then checks each bundle market
(moneyline / totals / spreads / team totals / BTTS / extra time) against its
replication price = sum of component state prices. "Any Other Score" is a mixed
bucket, so ambiguous bundles get an interval [lo, hi] instead of a point.

Usage:
  python3 scan.py <slug | gameId | search terms>   [--no-chart] [--out DIR]

Read-only. No keys, no execution.
"""

import json
import re
import ssl
import sys
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()


def get(path, **params):
    url = f"{GAMMA}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "poly-state-scan/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
        return json.load(r)


def resolve_game_id(query):
    """slug -> gameId, digits -> gameId, otherwise public-search."""
    if re.fullmatch(r"\d+", query):
        return int(query), query
    evs = get("/events", slug=query)
    if evs and evs[0].get("gameId"):
        return int(evs[0]["gameId"]), evs[0]["title"]
    d = get("/public-search", q=query, limit_per_type=10, events_status="all")
    for e in d.get("events", []):
        full = get("/events", slug=e["slug"])
        if full and full[0].get("gameId"):
            return int(full[0]["gameId"]), full[0]["title"]
    raise SystemExit(f"no game found for {query!r}")


def market_price(m):
    """(mid, half_spread, source) for outcome[0] (YES/Over/first outcome)."""
    bid, ask = m.get("bestBid"), m.get("bestAsk")
    if bid is not None and ask is not None and 0 < ask <= 1:
        return (bid + ask) / 2, (ask - bid) / 2, "book"
    last = m.get("lastTradePrice")
    if last is not None:
        return float(last), 0.01, "last"
    prices = json.loads(m.get("outcomePrices") or "[]")
    return (float(prices[0]) if prices else None), 0.01, "static"


# ---------------------------------------------------------------- state space

def build_state_space(events):
    """{(b,s): pi} + pi_other from the exact-score event."""
    ev = next((e for e in events if any(
        (m.get("sportsMarketType") == "soccer_exact_score") for m in e["markets"])), None)
    if ev is None:
        raise SystemExit("no exact-score event on this game — no state space")
    states, other, home, away = {}, None, None, None
    for m in ev["markets"]:
        title = m.get("groupItemTitle") or m["question"]
        mid, hs, _ = market_price(m)
        if mid is None:
            continue
        if re.search(r"any other score", title, re.I):
            other = (mid, hs)
            continue
        g = re.match(r"(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+)", title.strip())
        if not g:
            continue
        home, away = g.group(1).strip(), g.group(4).strip().rstrip("?")
        states[(int(g.group(2)), int(g.group(3)))] = (mid, hs)
    if other is None:
        other = (max(0.0, 1 - sum(p for p, _ in states.values())), 0.01)
    return states, other, home, away


# ------------------------------------------------- bundle payoff definitions
# payoff(b, s) -> bool for grid states; other_payoff -> True / False / None(mixed)
# "Other" means at least one side scored >= 4.

def classify(m, home, away):
    q = m["question"]
    t = m.get("sportsMarketType") or ""
    outcomes = json.loads(m.get("outcomes") or "[]")
    first = outcomes[0] if outcomes else "Yes"

    def team_side(name):
        if home.lower() in name.lower():
            return "home"
        if away.lower() in name.lower():
            return "away"
        return None

    if t == "moneyline":
        if re.search(r"draw", q, re.I):
            return f"Moneyline: Draw ({first})", lambda b, s: b == s, None
        side = team_side(q)
        if side:
            nm = home if side == "home" else away
            fn = (lambda b, s: b > s) if side == "home" else (lambda b, s: s > b)
            return f"Moneyline: {nm} win ({first})", fn, None

    if t == "totals":
        g = re.search(r"O/U\s+(\d+)\.5", q)
        if g:
            line = int(g.group(1))
            # Other bucket => one side >=4 => total >=4
            return (f"Total O/U {line}.5 ({first})",
                    lambda b, s: b + s > line,
                    True if line < 4 else None)

    if t == "soccer_team_totals":
        g = re.search(r"(.+?):\s*(.+?)\s+O/U\s+(\d+)\.5", q)
        if g:
            side = team_side(g.group(2))
            line = int(g.group(3))
            if side:
                nm = home if side == "home" else away
                fn = (lambda b, s: b > line) if side == "home" else (lambda b, s: s > line)
                return f"{nm} O/U {line}.5 ({first})", fn, None

    if t == "spreads":
        g = re.search(r"Spread:\s*(.+?)\s*\(-(\d+)\.5\)", q)
        if g:
            side = team_side(g.group(1))
            line = int(g.group(2))
            if side:
                nm = home if side == "home" else away
                fn = (lambda b, s: b - s > line) if side == "home" else (lambda b, s: s - b > line)
                return f"Spread {nm} -{line}.5 ({first})", fn, None

    if t == "both_teams_to_score":
        return f"BTTS ({first})", lambda b, s: b >= 1 and s >= 1, None

    if t == "soccer_extra_time":
        # knockout: extra time <=> regulation ends level
        return f"Extra time ({first})", lambda b, s: b == s, None

    return None  # not replicable from FT-score states


def replication_interval(fn, other_payoff, states, other):
    lo = sum(p for (b, s), (p, _) in states.items() if fn(b, s))
    if other_payoff is True:
        lo += other[0]
        return lo, lo
    if other_payoff is None:
        return lo, lo + other[0]
    return lo, lo


# ------------------------------------------------------------------- charts

INK, INK2, MUT = "#0b0b0b", "#52514e", "#898781"
SURF, GRID_C, BASE = "#fcfcfb", "#e1e0d9", "#c3c2b7"
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
       "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
BLUE, CRIT = "#2a78d6", "#d03b3b"


def draw_charts(states, other, home, away, rows, title, out_prefix):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    total = sum(p for p, _ in states.values()) + other[0]

    # -- chart 1: implied FT score distribution heatmap
    hb = sorted({b for b, _ in states}) or [0]
    sa = sorted({s for _, s in states}) or [0]
    fig, ax = plt.subplots(figsize=(7.2, 5.6), dpi=150)
    fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
    vmax = max([p for p, _ in states.values()] + [other[0], 1e-9])
    for b in hb:
        for s in sa:
            p = states.get((b, s), (0, 0))[0]
            c = cmap(min(1.0, p / vmax))
            ax.add_patch(plt.Rectangle((s, b), 0.96, 0.96, facecolor=c,
                                       edgecolor=SURF, linewidth=2))
            lum = p / vmax
            ax.text(s + 0.48, b + 0.48, f"{p*100:.1f}%", ha="center", va="center",
                    fontsize=10, color="#ffffff" if lum > 0.55 else INK)
    # Other tile
    ax.add_patch(plt.Rectangle((len(sa) + 0.25, 0), 0.96, 0.96,
                               facecolor=cmap(min(1.0, other[0] / vmax)),
                               edgecolor=SURF, linewidth=2))
    ax.text(len(sa) + 0.73, 0.48, f"{other[0]*100:.1f}%", ha="center", va="center",
            fontsize=10, color=INK)
    ax.text(len(sa) + 0.73, -0.32, "Other", ha="center", fontsize=9, color=INK2)
    ax.set_xticks([s + 0.48 for s in sa]); ax.set_xticklabels(sa, color=INK2)
    ax.set_yticks([b + 0.48 for b in hb]); ax.set_yticklabels(hb, color=INK2)
    ax.set_xlabel(f"{away} goals", color=INK2, fontsize=10)
    ax.set_ylabel(f"{home} goals", color=INK2, fontsize=10)
    ax.set_xlim(-0.1, len(sa) + 1.4); ax.set_ylim(len(hb), -0.75)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(f"{title} — market-implied exact-score distribution\n"
                 f"state prices from YES quotes · Σπ = {total:.3f}",
                 color=INK, fontsize=11, loc="left", pad=12)
    fig.tight_layout()
    f1 = f"{out_prefix}-distribution.png"
    fig.savefig(f1, facecolor=SURF); plt.close(fig)

    # -- chart 2: bundle price vs replication interval
    rows = sorted(rows, key=lambda r: abs(r["edge"]))
    fig, ax = plt.subplots(figsize=(8.6, 0.42 * len(rows) + 1.8), dpi=150)
    fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
    for i, r in enumerate(rows):
        if r["hi"] - r["lo"] < 5e-3:
            ax.plot([r["lo"]], [i], marker="|", ms=11, mew=2.5, color=BASE, zorder=2)
        else:
            ax.plot([r["lo"], r["hi"]], [i, i], color=BASE, lw=5,
                    solid_capstyle="butt", zorder=2)
        bad = r["flag"]
        ax.plot(r["mid"], i, "o", ms=7, color=CRIT if bad else BLUE, zorder=3,
                markeredgecolor=SURF, markeredgewidth=1.5)
        if bad:
            ax.annotate(f"{r['edge']*100:+.1f}¢", (r["mid"], i),
                        textcoords="offset points", xytext=(8, -3.5),
                        fontsize=8.5, color=CRIT)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["label"] for r in rows], fontsize=8.5, color=INK)
    ax.set_xlim(-0.02, 1.02)
    ax.set_xlabel("price ($)", color=INK2, fontsize=9)
    ax.grid(axis="x", color=GRID_C, lw=0.75); ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0, colors=INK2)
    n_bad = sum(r["flag"] for r in rows)
    ax.set_title(f"{title} — bundle price vs. state-space replication\n"
                 f"bar/tick = replication from score grid · dot = market mid\n"
                 f"red dot + label = mispriced beyond spread ({n_bad} flagged)",
                 color=INK, fontsize=11, loc="left", pad=12)
    fig.tight_layout()
    f2 = f"{out_prefix}-consistency.png"
    fig.savefig(f2, facecolor=SURF); plt.close(fig)
    return f1, f2


# --------------------------------------------------------------------- main

def main():
    argv = sys.argv[1:]
    no_chart = "--no-chart" in argv
    out_dir = "."
    if "--out" in argv:
        i = argv.index("--out")
        out_dir = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)

    game_id, _ = resolve_game_id(" ".join(args))
    events = get("/events", game_id=game_id, limit=50)
    title = min((e["title"] for e in events), key=len)
    live = any(e.get("live") for e in events)
    closed = all(e.get("closed") for e in events)
    print(f"# {title}  (game {game_id}, {len(events)} events, "
          f"{sum(len(e['markets']) for e in events)} markets)"
          f"{' [LIVE]' if live else ''}{' [CLOSED — degenerate prices]' if closed else ''}\n")

    states, other, home, away = build_state_space(events)
    total = sum(p for p, _ in states.values()) + other[0]
    print(f"state space: {len(states)} scores + Other   Σπ = {total:.3f}  "
          f"(vig/skew = {(total-1)*100:+.1f}¢ — normalized out before replication)")
    # normalize: raw Σπ != 1 is grid vig; without this, wide bundles (sums over
    # many states) inherit the whole skew and show phantom mispricing
    states = {k: (p / total, hs) for k, (p, hs) in states.items()}
    other = (other[0] / total, other[1])
    top = sorted(states.items(), key=lambda kv: -kv[1][0])[:5]
    print("top states: " + ", ".join(f"{b}-{s} {p*100:.1f}%" for (b, s), (p, _) in top)
          + f", Other {other[0]*100:.1f}%\n")

    rows, skipped = [], []
    for e in events:
        for m in e["markets"]:
            if (m.get("sportsMarketType") or "") == "soccer_exact_score":
                continue
            c = classify(m, home, away)
            if c is None:
                skipped.append(m.get("sportsMarketType") or m["question"])
                continue
            label, fn, other_pay = c
            mid, hs, src = market_price(m)
            if mid is None:
                continue
            lo, hi = replication_interval(fn, other_pay, states, other)
            tol = max(0.01, hs)
            edge = mid - hi if mid > hi else (mid - lo if mid < lo else 0.0)
            flag = abs(edge) > tol
            rows.append(dict(label=label, mid=mid, lo=lo, hi=hi,
                             edge=edge, flag=flag, src=src))

    rows.sort(key=lambda r: -abs(r["edge"]))
    w = max(len(r["label"]) for r in rows)
    print(f"{'bundle':<{w}}  {'market':>7}  {'replication':>13}  {'edge':>7}")
    for r in rows:
        rep = f"{r['lo']:.3f}" if r["hi"] - r["lo"] < 5e-4 else f"{r['lo']:.3f}–{r['hi']:.3f}"
        mark = "  <-- MISPRICED" if r["flag"] else ""
        print(f"{r['label']:<{w}}  {r['mid']:7.3f}  {rep:>13}  {r['edge']*100:+6.1f}¢{mark}")

    uniq = sorted(set(skipped))
    print(f"\nnot replicable from FT states (need HT joint dist): {', '.join(uniq)}")

    if not no_chart:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        f1, f2 = draw_charts(states, other, home, away, rows, title,
                             f"{out_dir}/{slug}")
        print(f"\ncharts: {f1}  {f2}")


if __name__ == "__main__":
    main()
