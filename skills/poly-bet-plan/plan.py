#!/usr/bin/env python3
"""
poly-bet-plan — mirror the top smart wallet in ONE Polymarket soccer game.

Pipeline:
  1. discover   — crawl top holders of the game's core markets (candidate pool)
  2. score      — lb-api all-time PnL/volume per wallet (SHARP/win+/FISH/MM)
  3. full book  — pull each ranked candidate's ENTIRE position in the game
                  (data-api /positions, all game markets incl. exact score);
                  the structural MM/hedge judgement runs on the FULL book,
                  never on the partial top-holder view
  4. mirror     — pick the top clean sharp, scale its NET game book to the
                  budget ($-value weighted, min leg $10, rounded to $5, FULL
                  budget deployed — no kelly, no cash reserve)
  5. execute    — CLOB entry band + slippage per mirrored leg, printed next to
                  the target wallet's own average entry

Output: console report (ready-to-paste leg table) + optional --json + two PNGs:
  *-distribution.png  market-implied exact-score distribution (context heatmap)
  *-pnl.png           ticket PnL per final score vs the aggregate sharp book

Usage:
  python3 plan.py <slug|gameId|team search> [--budget 100] [--out DIR]
                  [--json FILE] [--holders 10] [--wallet 0x..] [--no-chart]
                  [--legs JSON]

--wallet forces the mirror target; --legs renders a custom ticket instead:
  --legs '[{"q":"Will Spain win","side":"No","usd":40}, ...]'

Read-only. No keys, no execution. LLM-free: synthesis happens in-session.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# poly-state-scan (sibling skill) provides: get, resolve_game_id,
# build_state_space, classify, market_price. Look next to this file first so a
# repo checkout works, then fall back to the installed skill location.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_HERE, "..", "poly-state-scan"),
              os.path.expanduser("~/.claude/skills/poly-state-scan")):
    if os.path.isfile(os.path.join(_cand, "scan.py")):
        sys.path.insert(0, _cand)
        break
import scan as ss

DATA = "https://data-api.polymarket.com"
LB = "https://lb-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

SHARP_PNL, WINNER_PNL, FISH_PNL = 100_000, 10_000, -50_000

CORE_TYPES = {
    "moneyline", "totals", "spreads", "soccer_team_totals",
    "both_teams_to_score", "soccer_extra_time", "soccer_halftime_result",
    "soccer_first_to_score", "soccer_team_to_advance",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "poly-bet-plan/1.0",
                                               "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ss.SSL_CTX) as r:
        return json.load(r)


def fmt_usd(n):
    return ("-$" if n < 0 else "$") + f"{abs(n):,.0f}"


# ----------------------------------------------- state space (context only)
# Used for the distribution chart and per-score PnL projection. No mispricing
# detection — the edge here is the wallet, not the grid.

def state_space(events):
    states_raw, other_raw, home, away = ss.build_state_space(events)
    total = sum(p for p, _ in states_raw.values()) + other_raw[0]
    states = {k: (p / total, hs) for k, (p, hs) in states_raw.items()}
    other = (other_raw[0] / total, other_raw[1])
    return dict(states=states, other=other, home=home, away=away, sum_pi=total)


# --------------------------------------------------------------- smart money

def core_markets(events):
    ms = []
    for e in events:
        for m in e["markets"]:
            if (m.get("sportsMarketType") or "") in CORE_TYPES:
                ms.append(m)
    ms.sort(key=lambda m: -(float(m.get("volume") or 0)))
    return ms[:12]


def wallet_score(addr):
    """lb-api profile: move-analysis margin heuristic + sharp-scan PnL tiers."""
    try:
        p = fetch(f"{LB}/profit?window=all&limit=1&address={addr}")
        v = fetch(f"{LB}/volume?window=all&limit=1&address={addr}")
        P = p[0]["amount"] if p else None
        V = v[0]["amount"] if v else None
    except Exception:
        return dict(addr=addr, pnl=None, vol=None, margin=None, tag="?")
    marg = (P / V * 100) if (P is not None and V) else None
    tag = "·"
    if P is not None:
        if V and V >= 5e7 and (marg or 0) < 0.2:
            tag = "MM"
        elif P >= SHARP_PNL and (marg or 0) >= 1.0:
            tag = "SHARP"
        elif P >= SHARP_PNL:
            tag = "SHARP(thin)"
        elif P >= WINNER_PNL:
            tag = "win+"
        elif P <= FISH_PNL:
            tag = "FISH"
    return dict(addr=addr, pnl=P, vol=V, margin=marg, tag=tag)


def full_book(addr, game_mkts):
    """A wallet's ENTIRE open position in this game — every market of every
    event (incl. exact score), with entry price and running PnL. This is the
    only view a wallet may be judged or mirrored on; the top-holder crawl is
    discovery, not evidence."""
    try:
        pos = fetch(f"{DATA}/positions?user={addr}&limit=500")
    except Exception:
        return []
    legs = []
    for p in pos:
        m = game_mkts.get(p.get("conditionId"))
        if m is None:
            continue
        val = float(p.get("currentValue") or 0)
        if val < 25:
            continue
        outcomes = json.loads(m.get("outcomes") or '["Yes","No"]')
        si = int(p.get("outcomeIndex") or 0)
        legs.append(dict(m=m, q=m["question"], side_idx=si,
                         side=(p.get("outcome")
                               or (outcomes[si] if si < len(outcomes) else f"side{si}")),
                         shares=float(p.get("size") or 0),
                         avg=float(p.get("avgPrice") or 0),
                         cur=float(p.get("curPrice") or 0),
                         value=val, pnl=float(p.get("cashPnl") or 0)))
    legs.sort(key=lambda l: -l["value"])
    return legs


def net_legs(legs):
    """Collapse both-sides holdings in one market to the net directional leg.
    For a clean wallet this is identity; for a partially hedged book it keeps
    only the tilt (mirroring a hedge with $100 makes no sense)."""
    by_q = {}
    for l in legs:
        by_q.setdefault(l["q"], {})[min(l["side_idx"], 1)] = l
    out = []
    for q, sides in by_q.items():
        if len(sides) == 1:
            out.append(next(iter(sides.values())))
            continue
        big, small = sorted(sides.values(), key=lambda x: -x["shares"])
        net_sh = big["shares"] - small["shares"]
        if net_sh * big["cur"] < 25:
            continue
        l = dict(big)
        l["shares"] = net_sh
        l["value"] = net_sh * l["cur"]
        l["netted"] = True
        out.append(l)
    out.sort(key=lambda l: -l["value"])
    return out


def track_b(events, a, n_holders):
    markets = core_markets(events)
    game_mkts = {m["conditionId"]: m for e in events for m in e["markets"]
                 if m.get("conditionId")}
    seen = {}   # wallet -> display name (discovery only — NOT a judgement basis)
    for m in markets:
        try:
            toks = fetch(f"{DATA}/holders?market={m['conditionId']}&limit={n_holders + 4}")
        except Exception:
            continue
        for tok in toks[:2]:
            for h in (tok.get("holders") or [])[:n_holders]:
                w = h.get("proxyWallet")
                if not w:
                    continue
                name = h.get("name") or h.get("pseudonym") or w[:8]
                if re.match(r"^0x", name, re.I):
                    name = w[:8]
                seen.setdefault(w, name)

    # wallet profiles (threaded)
    with ThreadPoolExecutor(max_workers=8) as ex:
        profiles = {p["addr"]: p for p in ex.map(wallet_score, seen.keys())}

    # rank winners by all-time PnL, then pull FULL game books BEFORE judging
    cand = [w for w in seen if profiles[w]["tag"] in ("SHARP", "SHARP(thin)", "win+")]
    cand.sort(key=lambda w: -(profiles[w]["pnl"] or 0))
    cand = cand[:8]
    fish_w = sorted((w for w in seen if profiles[w]["tag"] == "FISH"),
                    key=lambda w: (profiles[w]["pnl"] or 0))[:4]
    with ThreadPoolExecutor(max_workers=8) as ex:
        books = dict(zip(cand + fish_w,
                         ex.map(lambda w: full_book(w, game_mkts), cand + fish_w)))

    # structural judgement on the FULL book: both sides of one market =
    # inventory/hedge shape; all-No across >=3 markets = MM inventory
    sharps = {}
    for w in cand:
        legs = books.get(w) or []
        if not legs:
            continue
        by_q = {}
        for l in legs:
            by_q.setdefault(l["q"], set()).add(l["side_idx"])
        hedged = any(len(s) > 1 for s in by_q.values())
        no_legs = [l for l in legs if l["side"] == "No"]
        all_no = len(no_legs) >= 3 and len(no_legs) == len(legs)
        sharps[w] = dict(name=seen[w], legs=legs, inventory=hedged or all_no)
    fish = {w: dict(name=seen[w], legs=books.get(w) or []) for w in fish_w
            if books.get(w)}

    # aggregate sharp book -> per-market NET stance + state-space payoff curve
    stance = {}  # question -> {side: net sharp shares}
    curve_legs, off_grid = [], []
    for w, h in sharps.items():
        for l in net_legs(h["legs"]):
            stance.setdefault(l["q"], {}).setdefault(l["side"], 0.0)
            stance[l["q"]][l["side"]] += l["shares"]
        for l in h["legs"]:   # curve uses raw legs: hedge pairs net out in payoff
            c = ss.classify(l["m"], a["home"], a["away"])
            if c is not None:
                _, fn, other_pay = c
                pays = fn if l["side_idx"] == 0 else (lambda b, s, f=fn: not f(b, s))
                op = other_pay if l["side_idx"] == 0 else (
                    None if other_pay is None else (not other_pay))
                curve_legs.append(dict(shares=l["shares"], pays=pays, other_pays=op,
                                       value=l["value"]))
            else:
                g = re.match(r"(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+)",
                             (l["m"].get("groupItemTitle") or ""))
                if g and (l["m"].get("sportsMarketType") == "soccer_exact_score"):
                    bb, ss_ = int(g.group(2)), int(g.group(3))
                    hit = (lambda b, s, bb=bb, ss_=ss_: b == bb and s == ss_)
                    pays = hit if l["side_idx"] == 0 else (lambda b, s, h2=hit: not h2(b, s))
                    op = (False if l["side_idx"] == 0 else True)
                    curve_legs.append(dict(shares=l["shares"], pays=pays, other_pays=op,
                                           value=l["value"]))
                else:
                    off_grid.append(dict(q=l["q"], side=l["side"], shares=l["shares"]))

    book_value = sum(cl["value"] for cl in curve_legs)

    def sharp_pnl_at(b, s, is_other=False):
        """forward PnL of the aggregate sharp book at a final score, per $100 of book."""
        pay = 0.0
        for cl in curve_legs:
            if is_other:
                if cl["other_pays"] is True:
                    pay += cl["shares"]
                elif cl["other_pays"] is None:
                    pay += cl["shares"] * 0.5  # unknowable inside Other; midpoint
            elif cl["pays"](b, s):
                pay += cl["shares"]
        if book_value <= 0:
            return 0.0
        return (pay - book_value) / book_value * 100.0

    return dict(profiles=profiles, n_seen=len(seen), sharps=sharps, fish=fish,
                stance=stance, sharp_pnl_at=sharp_pnl_at, book_value=book_value,
                off_grid=off_grid, n_curve_legs=len(curve_legs))


# ------------------------------------------------------------- executability

def orderbook(m, side_idx, budget):
    toks = json.loads(m.get("clobTokenIds") or "[]")
    if side_idx >= len(toks):
        return None
    try:
        book = fetch(f"{CLOB}/book?token_id={toks[side_idx]}")
    except Exception:
        return None
    asks = sorted(((float(x["price"]), float(x["size"])) for x in book.get("asks") or []),
                  key=lambda t: t[0])
    bids = sorted(((float(x["price"]), float(x["size"])) for x in book.get("bids") or []),
                  key=lambda t: -t[0])
    if not asks:
        return None
    # walk asks spending `budget` dollars (analyzer's _estimate_slippage, $-based)
    spent, filled = 0.0, 0.0
    for price, size in asks:
        cost = price * size
        take = min(cost, budget - spent)
        filled += take / price
        spent += take
        if spent >= budget - 1e-9:
            break
    best_ask = asks[0][0]
    best_bid = bids[0][0] if bids else None
    avg = spent / filled if filled else None
    depth_ask = sum(p * s for p, s in asks[:5])
    thin = "THIN" if depth_ask < 500 else ("MODERATE" if depth_ask < 5000 else "THICK")
    return dict(best_bid=best_bid, best_ask=best_ask, avg_fill=avg,
                fillable=spent >= budget - 1e-9, spent=spent,
                slippage=(avg - best_ask) if avg else None,
                top5_ask_depth_usd=depth_ask, liquidity=thin)


# ------------------------------------------------------------------- mirror

def short_label(l, a):
    c = ss.classify(l["m"], a["home"], a["away"])
    if c is not None:
        base = re.sub(r"\s*\([^)]*\)\s*$", "", c[0])   # strip "(Over)"-style suffix
    else:
        base = l["m"].get("groupItemTitle") or l["q"]
    return f"{l['side']} — {base}"


def mirror_legs(target_book, budget, max_legs=5):
    """Scale the target wallet's NET game book to the budget, weighted by the
    wallet's current $ per leg. Min leg $10, rounded to $5, FULL budget spent."""
    picked = [l for l in net_legs(target_book) if l["value"] > 0][:max_legs]
    usds = []
    while picked:
        tot = sum(l["value"] for l in picked)
        usds = [budget * l["value"] / tot for l in picked]
        if min(usds) >= 10:
            break
        picked, usds = picked[:-1], []   # value-sorted: drop the smallest leg
    if not picked:
        return []
    usds = [round(u / 5) * 5 for u in usds]
    usds[0] += budget - sum(usds)        # rounding remainder to the top leg
    return [dict(q=l["q"], m=l["m"], side_idx=l["side_idx"], side=l["side"],
                 usd=float(u), cost=None, target_avg=l["avg"],
                 target_cur=l["cur"], src="mirror")
            for l, u in zip(picked, usds)]


def legs_pnl_at(legs, a):
    """net PnL of the ticket legs per final score (and Other)."""
    home, away = a["home"], a["away"]
    resolved = []
    for l in legs:
        c = ss.classify(l["m"], home, away)
        price = l["cost"]
        shares = l["usd"] / price if price else 0
        if c is not None:
            _, fn, other_pay = c
            pays = fn if l["side_idx"] == 0 else (lambda b, s, f=fn: not f(b, s))
            op = other_pay if l["side_idx"] == 0 else (
                None if other_pay is None else (not other_pay))
        else:
            g = re.match(r"(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+)",
                         (l["m"].get("groupItemTitle") or ""))
            if not g:
                continue
            bb, ss_ = int(g.group(2)), int(g.group(3))
            hit = (lambda b, s, bb=bb, ss_=ss_: b == bb and s == ss_)
            pays = hit if l["side_idx"] == 0 else (lambda b, s, h2=hit: not h2(b, s))
            op = False if l["side_idx"] == 0 else True
        resolved.append(dict(shares=shares, usd=l["usd"], pays=pays, other_pays=op))

    def at(bb, ss_, is_other=False):
        pnl = 0.0
        for r in resolved:
            if is_other:
                pay = r["shares"] if r["other_pays"] is True else (
                    r["shares"] * 0.5 if r["other_pays"] is None else 0.0)
            else:
                pay = r["shares"] if r["pays"](bb, ss_) else 0.0
            pnl += pay - r["usd"]
        return pnl
    return at


# --------------------------------------------------------------------- charts

def draw_pnl_chart(a, b, legs, title, path):
    """Projected-PnL heatmap in the same grid layout as the distribution chart.
    Cell = ticket net PnL at that final score (diverging blue=win / red=loss,
    gray at zero); small second line = aggregate sharp book per $100."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    at = legs_pnl_at(legs, a)
    keys = sorted(a["states"].keys())
    vals = {k: at(*k) for k in keys}
    v_other = at(0, 0, is_other=True)
    sharp = {k: b["sharp_pnl_at"](*k) for k in keys}
    s_other = b["sharp_pnl_at"](0, 0, is_other=True)

    cmap = LinearSegmentedColormap.from_list(
        "div", ["#c73634", "#e34948", "#f0efec", "#6da7ec", "#2a78d6", "#1c5cab"])
    allv = list(vals.values()) + [v_other]
    norm = TwoSlopeNorm(vmin=min(min(allv), -1), vcenter=0.0,
                        vmax=max(max(allv), 1))

    hb = sorted({x for x, _ in keys}); sa = sorted({y for _, y in keys})
    fig, ax = plt.subplots(figsize=(7.6, 5.9), dpi=150)
    fig.patch.set_facecolor(ss.SURF); ax.set_facecolor(ss.SURF)

    def cell(x, y, v, sv):
        ax.add_patch(plt.Rectangle((x, y), 0.96, 0.96, facecolor=cmap(norm(v)),
                                   edgecolor=ss.SURF, linewidth=2))
        dark = abs(norm(v) - 0.5) > 0.3
        ink = "#ffffff" if dark else ss.INK
        ink2 = "#e8e8e8" if dark else ss.INK2
        ax.text(x + 0.48, y + 0.40, f"{v:+,.0f}", ha="center", va="center",
                fontsize=10, fontweight="bold", color=ink)
        ax.text(x + 0.48, y + 0.72, f"shp {sv:+,.0f}", ha="center", va="center",
                fontsize=7, color=ink2)

    for bb in hb:
        for ss_ in sa:
            cell(ss_, bb, vals[(bb, ss_)], sharp[(bb, ss_)])
    cell(len(sa) + 0.25, 0, v_other, s_other)
    ax.text(len(sa) + 0.73, -0.32, "Other", ha="center", fontsize=9, color=ss.INK2)

    ax.set_xticks([s + 0.48 for s in sa]); ax.set_xticklabels(sa, color=ss.INK2)
    ax.set_yticks([bb + 0.48 for bb in hb]); ax.set_yticklabels(hb, color=ss.INK2)
    ax.set_xlabel(f"{a['away']} goals", color=ss.INK2, fontsize=10)
    ax.set_ylabel(f"{a['home']} goals", color=ss.INK2, fontsize=10)
    ax.set_xlim(-0.1, len(sa) + 1.4); ax.set_ylim(len(hb), -0.75)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    total = sum(l["usd"] for l in legs)
    ax.set_title(f"{title} — projected PnL by final score (\\${total:.0f} ticket)\n"
                 f"blue = you win, red = you lose · small line = sharp aggregate "
                 f"book per \\$100",
                 color=ss.INK, fontsize=11, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(path, facecolor=ss.SURF); plt.close(fig)


def draw_distribution(a, title, path):
    # heatmap identical to poly-state-scan's chart 1
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    states, other, home, away = a["states"], a["other"], a["home"], a["away"]
    cmap = LinearSegmentedColormap.from_list("seq", ss.SEQ)
    hb = sorted({b for b, _ in states}); sa = sorted({s for _, s in states})
    fig, ax = plt.subplots(figsize=(7.2, 5.6), dpi=150)
    fig.patch.set_facecolor(ss.SURF); ax.set_facecolor(ss.SURF)
    vmax = max([p for p, _ in states.values()] + [other[0], 1e-9])
    for b in hb:
        for s in sa:
            p = states.get((b, s), (0, 0))[0]
            ax.add_patch(plt.Rectangle((s, b), 0.96, 0.96,
                                       facecolor=cmap(min(1.0, p / vmax)),
                                       edgecolor=ss.SURF, linewidth=2))
            ax.text(s + 0.48, b + 0.48, f"{p*100:.1f}%", ha="center", va="center",
                    fontsize=10, color="#ffffff" if p / vmax > 0.55 else ss.INK)
    ax.add_patch(plt.Rectangle((len(sa) + 0.25, 0), 0.96, 0.96,
                               facecolor=cmap(min(1.0, other[0] / vmax)),
                               edgecolor=ss.SURF, linewidth=2))
    ax.text(len(sa) + 0.73, 0.48, f"{other[0]*100:.1f}%", ha="center", va="center",
            fontsize=10, color="#ffffff" if other[0] / vmax > 0.55 else ss.INK)
    ax.text(len(sa) + 0.73, -0.32, "Other", ha="center", fontsize=9, color=ss.INK2)
    ax.set_xticks([s + 0.48 for s in sa]); ax.set_xticklabels(sa, color=ss.INK2)
    ax.set_yticks([b + 0.48 for b in hb]); ax.set_yticklabels(hb, color=ss.INK2)
    ax.set_xlabel(f"{away} goals", color=ss.INK2, fontsize=10)
    ax.set_ylabel(f"{home} goals", color=ss.INK2, fontsize=10)
    ax.set_xlim(-0.1, len(sa) + 1.4); ax.set_ylim(len(hb), -0.75)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(f"{title} — market-implied exact-score distribution (normalized)",
                 color=ss.INK, fontsize=11, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(path, facecolor=ss.SURF); plt.close(fig)


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--budget", type=float, default=100.0)
    ap.add_argument("--out", default=".")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--legs", help="JSON list [{q, side, usd}] to render a custom ticket")
    ap.add_argument("--wallet", help="force this address as the mirror target")
    ap.add_argument("--holders", type=int, default=10)
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args()

    game_id, _ = ss.resolve_game_id(" ".join(args.query))
    events = ss.get("/events", game_id=game_id, limit=50)
    title = min((e["title"] for e in events), key=len)
    main_event = min(events, key=lambda e: len(e["title"]))
    if all(e.get("closed") for e in events):
        raise SystemExit(f"{title}: game is closed — prices are 0/1, nothing to plan")
    print(f"# {title}  (game {game_id}, budget ${args.budget:.0f})"
          f"{'  [LIVE]' if any(e.get('live') for e in events) else ''}\n")

    a = state_space(events)
    b = track_b(events, a, args.holders)
    print(f"## smart money   {b['n_seen']} wallets crawled | "
          f"{len(b['sharps'])} sharp full books pulled | {len(b['fish'])} fish")

    print("\n   candidate full game books (ranked by all-time PnL):")
    for w, h in b["sharps"].items():
        p = b["profiles"][w]
        marg = f"  margin {p['margin']:.1f}%" if p["margin"] is not None else ""
        inv = "  [INVENTORY/HEDGED — net tilt only, not a mirror target]" \
            if h["inventory"] else ""
        pnl_game = sum(l["pnl"] for l in h["legs"])
        print(f"   {h['name']}  ({w[:10]}…)  all-time {fmt_usd(p['pnl'])}{marg}  "
              f"game book {fmt_usd(sum(l['value'] for l in h['legs']))} "
              f"(pnl {fmt_usd(pnl_game)}){inv}")
        for l in h["legs"][:8]:
            print(f"      {l['side']:<8} {l['shares']/1000:>7.1f}k sh @{l['avg']:.2f} "
                  f"(now {l['cur']:.2f}, {fmt_usd(l['pnl'])}) — {l['q']}")
        if len(h["legs"]) > 8:
            print(f"      … +{len(h['legs']) - 8} more legs")

    print("\n   sharp aggregate NET stance (full books, hedges netted):")
    for q, sides in sorted(b["stance"].items(),
                           key=lambda kv: -max(kv[1].values()))[:10]:
        top = max(sides.items(), key=lambda kv: kv[1])
        rest = {k: v for k, v in sides.items() if k != top[0]}
        extra = "" if not rest else "  (vs " + ", ".join(
            f"{k} {v/1000:.0f}k" for k, v in rest.items()) + ")"
        print(f"   {top[1]/1000:>7.0f}k sh  {top[0]:<8} — {q}{extra}")

    if b["fish"]:
        print("\n   fade watch (proven losers, full books):")
        for w, h in b["fish"].items():
            p = b["profiles"][w]
            legs_s = "; ".join(f"{l['side']} {l['q'][:40]} @{l['avg']:.2f}"
                               for l in h["legs"][:3])
            print(f"   {h['name']} {fmt_usd(p['pnl'])} — {legs_s}")

    # ------------------------------------------------------- mirror target
    if args.legs:
        target_w, target = None, None
        spec = json.loads(args.legs)
        legs = []
        all_markets = [m for e in events for m in e["markets"]]
        for sl in spec:
            m = next((m for m in all_markets
                      if sl["q"].lower() in m["question"].lower()), None)
            if m is None:
                raise SystemExit(f"--legs: no market matches {sl['q']!r}")
            outcomes = json.loads(m.get("outcomes") or '["Yes","No"]')
            side_idx = next((i for i, o in enumerate(outcomes)
                             if o.lower() == sl["side"].lower()), 0)
            legs.append(dict(q=m["question"], m=m, side_idx=side_idx,
                             side=outcomes[side_idx], usd=float(sl["usd"]),
                             cost=None, target_avg=None, target_cur=None,
                             src="custom"))
    else:
        if args.wallet:
            target_w = args.wallet
            gm = {m["conditionId"]: m for e in events for m in e["markets"]
                  if m.get("conditionId")}
            target = dict(name=args.wallet[:8], legs=full_book(args.wallet, gm),
                          inventory=False)
            if not target["legs"]:
                raise SystemExit(f"--wallet {args.wallet}: no open position in this game")
        else:
            target_w = next((w for w, h in b["sharps"].items()
                             if not h["inventory"]), None)
            if target_w is None:
                raise SystemExit("no clean (non-inventory) sharp wallet found to "
                                 "mirror — name the closest candidate by hand")
            target = b["sharps"][target_w]
        legs = mirror_legs(target["legs"], args.budget)
        p = b["profiles"].get(target_w) or wallet_score(target_w)
        marg = f", margin {p['margin']:.1f}%" if p.get("margin") is not None else ""
        print(f"\n## mirror target: {target['name']}  ({target_w})  "
              f"all-time {fmt_usd(p['pnl'])}{marg}")

    # entry band per leg (budget-weighted book walk), cost = realistic fill
    for l in legs:
        bk = orderbook(l["m"], l["side_idx"], l["usd"])
        l["book"] = bk
        l["cost"] = ((bk or {}).get("avg_fill") or (bk or {}).get("best_ask")
                     or l.get("target_cur") or 0.5)

    print(f"\n## mirror ticket — full budget deployed "
          f"(${sum(l['usd'] for l in legs):.0f} of ${args.budget:.0f})")
    medals = ["🥇", "🥈", "🥉"]
    print("\n| 注 | 价 | 投入 | 份数 | 命中回款 |")
    print("|---|---|---|---|---|")
    for i, l in enumerate(legs):
        shares = l["usd"] / l["cost"] if l["cost"] else 0.0
        medal = medals[i] if i < len(medals) else "•"
        print(f"| {medal} {short_label(l, a)} | {l['cost']*100:.1f}¢ "
              f"| **${l['usd']:.0f}** | {shares:.1f} | **${shares:.2f}** |")
    print(f"\nlink: https://polymarket.com/event/{main_event.get('slug')}")

    print("\n   execution per leg (entry band vs target's own entry):")
    for l in legs:
        bk = l["book"] or {}
        band = (f"entry {bk.get('best_ask'):.3f}→{bk.get('avg_fill'):.3f}  "
                f"{bk.get('liquidity', '?')}" if bk.get("avg_fill") else "no book")
        tgt = (f"  | target entered @{l['target_avg']:.2f}"
               if l.get("target_avg") else "")
        print(f"   {l['side']:<8} {l['q'][:44]:<46} {band}{tgt}")

    at = legs_pnl_at(legs, a)
    keys = sorted(a["states"].keys())
    worst = min(keys, key=lambda k: at(*k)); best = max(keys, key=lambda k: at(*k))
    print(f"\n   scenario PnL: best {best[0]}-{best[1]} {at(*best):+,.0f} | "
          f"worst {worst[0]}-{worst[1]} {at(*worst):+,.0f} | "
          f"EV {sum(at(*k)*a['states'][k][0] for k in keys) + at(0,0,True)*a['other'][0]:+,.1f}")

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not args.no_chart:
        f1 = f"{args.out}/{slug}-distribution.png"
        f2 = f"{args.out}/{slug}-pnl.png"
        draw_distribution(a, title, f1)
        draw_pnl_chart(a, b, legs, title, f2)
        print(f"\ncharts: {f1}  {f2}")

    if args.json_out:
        out = dict(
            title=title, game_id=game_id, budget=args.budget,
            sum_pi=a["sum_pi"],
            states={f"{k[0]}-{k[1]}": v[0] for k, v in a["states"].items()},
            other=a["other"][0],
            stance=b["stance"],
            target=target_w,
            sharps={w: dict(name=h["name"], pnl=b["profiles"][w]["pnl"],
                            inventory=h["inventory"],
                            legs=[{k: l[k] for k in
                                   ("q", "side", "shares", "avg", "cur", "value", "pnl")}
                                  for l in h["legs"]])
                    for w, h in b["sharps"].items()},
            fish={w: dict(name=h["name"], pnl=b["profiles"][w]["pnl"])
                  for w, h in b["fish"].items()},
            legs=[{k: l[k] for k in ("q", "side", "usd", "cost", "target_avg")}
                  | dict(book=l.get("book")) for l in legs],
        )
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=1, default=str)
        print(f"json: {args.json_out}")


if __name__ == "__main__":
    main()
