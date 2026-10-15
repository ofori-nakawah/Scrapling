"""Every SportyBet football market that goals settle, as one rule each: the model behind the morning
and on-demand analysis and the live cushion.

A match is its goal sequence: [(half, "h"|"a"), ...]. Each market outcome becomes a rule
`rule(m) -> True (wins) | False (loses) | None (void)` read off that sequence, so one interpreter
serves results, totals, handicaps, both-teams-to-score, combos, halves, "X goals in a row",
"lead by N at any time", "win from behind", rest-of-match markets and the rest.

- prob(rule, lh, la): the pre-match chance an outcome wins, from a Poisson goal model (expected
  goals lh/la, FIRST_HALF_SHARE of them before half-time) simulated SIMS times. Void counts as not won.
- cushion(rule, halves, half_now): the fewest further goals, by either side in any order, that lose
  the bet. Live feeds give the goals per half but not their order, so every order of the goals
  already scored is tried and the worst one counts.

Markets that goals cannot settle (bookings, corners, shots, players, minute windows) get no rule.
"""
from __future__ import annotations

import itertools
import math
import random
import re

FIRST_HALF_SHARE = 0.45
SIMS = 4000
MAX_EXTRA = 4            # cushion search depth; a bet surviving 4 more goals counts as 5


# ---- the match --------------------------------------------------------------------------------

class M:
    __slots__ = ("seq", "h", "a", "h1", "a1", "h2", "a2")

    def __init__(self, seq):
        self.seq = seq
        self.h1 = sum(1 for p, t in seq if p == 1 and t == "h")
        self.a1 = sum(1 for p, t in seq if p == 1 and t == "a")
        self.h = sum(1 for _, t in seq if t == "h")
        self.a = len(seq) - self.h
        self.h2, self.a2 = self.h - self.h1, self.a - self.a1


def res(h, a):
    return "H" if h > a else "A" if a > h else "D"


# ---- outcome text -> tests on a (home, away) score -------------------------------------------

RESULT = {"home": "H", "draw": "D", "away": "A", "1": "H", "x": "D", "2": "A"}


def num(s):
    return float(re.search(r"-?\d+(?:\.\d+)?", s).group())


def rng(text):
    """'2-3' -> (2, 3); '4+' -> (4, inf); '0' -> (0, 0); 'No goal' -> (0, 0)."""
    t = text.strip().lower()
    if t in ("no goal", "none"):
        return 0, 0
    if m := re.fullmatch(r"(\d+)\s*-\s*(\d+)", t):
        return int(m[1]), int(m[2])
    if m := re.fullmatch(r"(\d+)\+", t):
        return int(m[1]), math.inf
    if m := re.fullmatch(r"(\d+)", t):
        return int(m[1]), int(m[1])
    return None


def over_under(text, n):
    """'Over 2.5' / 'Under 3' on n goals: True, False, or None on a whole-line push."""
    t = text.lower()
    line = num(t)
    if n == line:
        return None
    return (n > line) if t.startswith("over") else (n < line) if t.startswith("under") else None


def asian(team_goals, other_goals, line):
    """Asian handicap from the backed side: quarter lines are two bets; any losing half loses."""
    parts = [line] if (line * 4) % 2 == 0 else [line - 0.25, line + 0.25]
    outs = []
    for p in parts:
        d = team_goals + p - other_goals
        outs.append(None if d == 0 else d > 0)
    if False in outs:
        return False
    return True if True in outs else None


def side_test(word):
    """A result word or a double chance ('Home or Draw', 'Home/Draw', '1X') -> set of results."""
    w = word.strip().lower().replace(" or ", "/")
    if w in RESULT:
        return {RESULT[w]}
    if "/" in w:
        return {RESULT.get(p.strip()) for p in w.split("/")} - {None} or None
    if w in ("1x", "12", "x2"):
        return {RESULT[c] for c in w}
    return None


def part(text, h, a):
    """One clause of a combo outcome ('Home', 'Home/Draw', 'Over 2.5', 'Under 1.5', 'Yes', 'No')."""
    t = text.strip()
    tl = t.lower()
    if tl.startswith(("over", "under")):
        return over_under(t, h + a)
    if tl in ("yes", "gg"):
        return h > 0 and a > 0
    if tl in ("no", "ng"):
        return not (h > 0 and a > 0)
    s = side_test(t)
    if s:
        return res(h, a) in s
    raise ValueError(t)


# ---- market id -> rule --------------------------------------------------------------------------

# Which goals a "score" market reads: the whole match, the first half, or the second half.
FT = lambda m: (m.h, m.a)          # noqa: E731
H1 = lambda m: (m.h1, m.a1)        # noqa: E731
H2 = lambda m: (m.h2, m.a2)        # noqa: E731

SCORE_MARKETS = {  # id: (period, kind)
    1: (FT, "1x2"), 60: (H1, "1x2"), 83: (H2, "1x2"),
    10: (FT, "dc"), 63: (H1, "dc"), 85: (H2, "dc"),
    11: (FT, "dnb"), 64: (H1, "dnb"), 86: (H2, "dnb"),
    12: (FT, "hnb"), 13: (FT, "anb"),
    14: (FT, "hcp"), 65: (H1, "hcp"), 87: (H2, "hcp"),
    16: (FT, "asian"), 66: (H1, "asian"), 88: (H2, "asian"),
    18: (FT, "ou"), 68: (H1, "ou"), 90: (H2, "ou"),
    19: (FT, "ou_home"), 69: (H1, "ou_home"), 91: (H2, "ou_home"),
    20: (FT, "ou_away"), 70: (H1, "ou_away"), 92: (H2, "ou_away"),
    21: (FT, "exact"), 71: (H1, "exact"), 93: (H2, "exact"),
    23: (FT, "exact_home"), 24: (FT, "exact_away"),
    25: (FT, "range"), 548: (FT, "range"), 552: (H1, "range"), 553: (H2, "range"),
    549: (FT, "range_home"), 550: (FT, "range_away"),
    450001: (FT, "range"), 450002: (FT, "range_home"), 450003: (FT, "range_away"), 810001: (H1, "range"),
    450004: (FT, "excluded"), 450005: (FT, "excluded_home"), 450006: (FT, "excluded_away"), 810002: (H1, "excluded"),
    26: (FT, "odd"), 74: (H1, "odd"), 94: (H2, "odd"),
    27: (FT, "odd_home"), 28: (FT, "odd_away"), 900032: (H1, "odd_home"), 900031: (H1, "odd_away"),
    900029: (H2, "odd_home"), 900030: (H2, "odd_away"),
    29: (FT, "gg"), 75: (H1, "gg"), 95: (H2, "gg"), 60000: (FT, "gg2"),
    30: (FT, "teams_to_score"),
    31: (FT, "cs_home"), 76: (H1, "cs_home"), 96: (H2, "cs_home"),
    32: (FT, "cs_away"), 77: (H1, "cs_away"), 97: (H2, "cs_away"),
    33: (FT, "wtn_home"), 900036: (H1, "wtn_home"), 900038: (H2, "wtn_home"),
    34: (FT, "wtn_away"), 900037: (H1, "wtn_away"), 900052: (H2, "wtn_away"),
    35: (FT, "combo"), 36: (FT, "combo"), 37: (FT, "combo"), 546: (FT, "combo"), 547: (FT, "combo"),
    78: (H1, "combo"), 79: (H1, "combo"), 542: (H1, "combo"), 900042: (H1, "combo"),
    543: (H2, "combo"), 544: (H2, "combo"), 545: (H2, "combo"), 900043: (H2, "combo"),
    45: (FT, "cs"), 900053: (FT, "cs"), 81: (H1, "cs"), 98: (H2, "cs"),
    900041: (FT, "nodraw_gg"),
}
# "X or Y" markets (Yes/No): the clause after "or".
OR_MARKETS = {854: ("H", "Over 2.5"), 855: ("H", "Under 2.5"), 856: ("D", "Over 2.5"), 857: ("D", "Under 2.5"),
              858: ("A", "Over 2.5"), 859: ("A", "Under 2.5"), 860: ("H", "gg"), 861: ("D", "gg"), 862: ("A", "gg"),
              863: ("H", "cs"), 864: ("D", "cs"), 865: ("A", "cs")}
FIRST_HALF_IDS = {60, 63, 64, 65, 66, 68, 69, 70, 71, 74, 75, 76, 77, 78, 79, 81, 542, 552, 810001, 810002,
                  900031, 900032, 900036, 900037, 900042}


def yes_no(desc):
    d = desc.strip().lower()
    return True if d == "yes" else False if d == "no" else None


def score_rule(kind, period, spec, desc):
    d = desc.strip()
    dl = d.lower()
    if kind == "1x2":
        want = RESULT.get(dl)
        return lambda m: res(*period(m)) == want
    if kind == "dc":
        want = side_test(d)
        return lambda m: res(*period(m)) in want
    if kind in ("dnb", "hnb", "anb"):
        want = RESULT.get(dl)
        void = {"dnb": "D", "hnb": "H", "anb": "A"}[kind]
        return lambda m: (None if res(*period(m)) == void else res(*period(m)) == want)
    if kind == "hcp":                   # European handicap, spec hcp=a:b added to home:away
        x, y = map(int, re.search(r"hcp=(\d+):(\d+)", spec).groups())
        want = RESULT.get(dl.split(" ")[0])
        return lambda m: res(period(m)[0] + x, period(m)[1] + y) == want
    if kind == "asian":                 # outcome text carries the backed side's own line
        side = dl.split(" ")[0]
        line = num(d)
        if side == "home":
            return lambda m: asian(period(m)[0], period(m)[1], line)
        return lambda m: asian(period(m)[1], period(m)[0], line)
    if kind == "ou":
        return lambda m: over_under(d, sum(period(m)))
    if kind in ("ou_home", "ou_away"):
        i = 0 if kind == "ou_home" else 1
        return lambda m: over_under(d, period(m)[i])
    if kind.startswith(("exact", "range")):
        r = rng(d)
        if not r:
            return None
        lo, hi = r
        pick = {"exact": sum, "range": sum, "exact_home": lambda s: s[0], "range_home": lambda s: s[0],
                "exact_away": lambda s: s[1], "range_away": lambda s: s[1]}[kind]
        return lambda m: lo <= pick(period(m)) <= hi
    if kind.startswith("excluded"):
        r = rng(d)
        if not r:
            return None
        lo, hi = r
        pick = {"excluded": sum, "excluded_home": lambda s: s[0], "excluded_away": lambda s: s[1]}[kind]
        return lambda m: not (lo <= pick(period(m)) <= hi)
    if kind.startswith("odd"):
        pick = {"odd": sum, "odd_home": lambda s: s[0], "odd_away": lambda s: s[1]}[kind]
        odd = dl.startswith("odd")
        return lambda m: (pick(period(m)) % 2 == 1) == odd
    if kind in ("gg", "gg2"):
        need = 2 if kind == "gg2" else 1
        yes = yes_no(d)
        return None if yes is None else (lambda m: (min(period(m)) >= need) == yes)
    if kind == "teams_to_score":
        want = {"none": (False, False), "only home": (True, False), "only away": (False, True), "both teams": (True, True)}.get(dl)
        return None if want is None else (lambda m: (period(m)[0] > 0, period(m)[1] > 0) == want)
    if kind in ("cs_home", "cs_away"):
        i = 1 if kind == "cs_home" else 0          # home clean sheet = away scored 0
        yes = yes_no(d) if yes_no(d) is not None else {"39": True, "40": False}.get(d)
        return None if yes is None else (lambda m: (period(m)[i] == 0) == yes)
    if kind in ("wtn_home", "wtn_away"):
        home = kind == "wtn_home"
        yes = yes_no(d)
        def wtn(m):
            h, a = period(m)
            return (h > a and a == 0) if home else (a > h and h == 0)
        return None if yes is None else (lambda m: wtn(m) == yes)
    if kind == "combo":                 # 'Home & Yes', 'Over 2.5 & No', 'Home/Draw & Under 1.5'
        clauses = [c for c in re.split(r"\s*&\s*", d) if c]
        try:
            part(clauses[0], 0, 0)
            for c in clauses[1:]:
                part(c, 0, 0)
        except (ValueError, AttributeError):
            return None
        def combo(m):
            h, a = period(m)
            outs = [part(c, h, a) for c in clauses]
            return None if None in outs else all(outs)
        return combo
    if kind == "cs":
        s = re.fullmatch(r"(\d+)\s*:\s*(\d+)", d)
        if not s:
            return None
        x, y = int(s[1]), int(s[2])
        return lambda m: period(m) == (x, y)
    if kind == "nodraw_gg":
        yes = yes_no(d) if yes_no(d) is not None else {"39": True, "40": False}.get(d)
        def ndgg(m):
            h, a = period(m)
            return h != a and h > 0 and a > 0
        return None if yes is None else (lambda m: ndgg(m) == yes)
    return None


def run_rule(team, n):
    """Some side ('h', 'a' or None for either) scores n or more goals in a row."""
    def has(m):
        best = cur = 0
        last = None
        for _, t in m.seq:
            cur = cur + 1 if t == last else 1
            last = t
            if team in (None, t):
                best = max(best, cur)
        return best >= n
    return has


def lead_rule(team, n):
    """Some side leads by n or more at any time."""
    def has(m):
        h = a = 0
        for _, t in m.seq:
            h, a = h + (t == "h"), a + (t == "a")
            if (team in (None, "h") and h - a >= n) or (team in (None, "a") and a - h >= n):
                return True
        return False
    return has


def from_behind(team):
    def won(m):
        h = a = 0
        trailed = False
        for _, t in m.seq:
            h, a = h + (t == "h"), a + (t == "a")
            if (team == "h" and a > h) or (team == "a" and h > a):
                trailed = True
        return trailed and ((h > a) if team == "h" else (a > h))
    return won


def nth_goal(n, want):
    def g(m):
        if len(m.seq) < n:
            return want == "none"
        return {"h": "home", "a": "away"}[m.seq[n - 1][1]] == want
    return g


def rule(mid, spec, desc, mdesc=""):
    """(rule, scope) for one outcome, or None when goals cannot settle it. scope is "1H" for markets
    settled at half-time, else "FT"."""
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        return None
    spec, desc = spec or "", (desc or "").strip()
    scope = "1H" if mid in FIRST_HALF_IDS else "FT"
    try:
        if mid in SCORE_MARKETS:
            period, kind = SCORE_MARKETS[mid]
            r = score_rule(kind, period, spec, desc)
            return (r, scope) if r else None
        if mid in OR_MARKETS:
            first, second = OR_MARKETS[mid]
            yes = yes_no(desc)
            if yes is None:
                return None
            def either(m, first=first, second=second):
                ok = res(m.h, m.a) == first
                if second == "gg":
                    ok = ok or (m.h > 0 and m.a > 0)
                elif second == "cs":
                    ok = ok or m.h == 0 or m.a == 0
                else:
                    ok = ok or bool(over_under(second, m.h + m.a))
                return ok == yes
            return either, scope
        yes = yes_no(desc)
        if mid in (60010, 60011, 60012, 60020, 60021, 60022) and yes is not None:
            n = 2 if mid < 60020 else 3
            team = {0: None, 1: "h", 2: "a"}[mid % 10]
            f = run_rule(team, n)
            return (lambda m: f(m) == yes), scope
        if 60300 <= mid <= 60308 and yes is not None:
            k = mid - 60300
            team, n = (None, "h", "a")[k // 3], k % 3 + 1
            f = lead_rule(team, n)
            return (lambda m: f(m) == yes), scope
        if mid in (900383, 900384) and yes is not None:
            f = from_behind("h" if mid == 900383 else "a")
            return (lambda m: f(m) == yes), scope
        if mid in (60100, 60200):       # early payout: wins if the side ever leads by 2 (1), else on the result
            n = 2 if mid == 60100 else 1
            want = RESULT.get(desc.lower())
            if want in ("H", "A"):
                f = lead_rule("h" if want == "H" else "a", n)
                return (lambda m: f(m) or res(m.h, m.a) == want), scope
            if want == "D":
                return (lambda m: res(m.h, m.a) == "D"), scope
            return None
        if mid == 8:                    # n-th goal
            n = int(re.search(r"goalnr=(\d+)", spec).group(1)) if "goalnr" in spec else 1
            want = desc.lower()
            return (nth_goal(n, want), scope) if want in ("home", "away", "none") else None
        if mid == 9:
            want = desc.lower()
            def last(m):
                if not m.seq:
                    return want == "none"
                return {"h": "home", "a": "away"}[m.seq[-1][1]] == want
            return (last, scope) if want in ("home", "away", "none") else None
        if mid == 47:                   # half-time / full-time
            ht, ft = [RESULT.get(p.strip().lower()) for p in desc.split("/")]
            return (lambda m: res(m.h1, m.a1) == ht and res(m.h, m.a) == ft), scope
        if mid in (48, 49, 50, 51) and yes is not None:
            team = "h" if mid in (48, 50) else "a"
            both = mid in (48, 49)
            def halves(m):
                w1 = (m.h1 > m.a1) if team == "h" else (m.a1 > m.h1)
                w2 = (m.h2 > m.a2) if team == "h" else (m.a2 > m.h2)
                return (w1 and w2) if both else (w1 or w2)
            return (lambda m: halves(m) == yes), scope
        if mid in (52, 53, 54):
            want = desc.lower()
            def high(m):
                x, y = {52: (m.h1 + m.a1, m.h2 + m.a2), 53: (m.h1, m.h2), 54: (m.a1, m.a2)}[mid]
                got = "1st half" if x > y else "2nd half" if y > x else "equal"
                return got == want
            return high, scope
        if mid == 55:                   # 1st/2nd half GG: 'Yes/No'
            a1, a2 = [p.strip().lower() == "yes" for p in desc.split("/")]
            return (lambda m: (m.h1 > 0 and m.a1 > 0) == a1 and (m.h2 > 0 and m.a2 > 0) == a2), scope
        if mid in (56, 57) and yes is not None:
            if mid == 56:
                return (lambda m: (m.h1 > 0 and m.h2 > 0) == yes), scope
            return (lambda m: (m.a1 > 0 and m.a2 > 0) == yes), scope
        if mid in (58, 59) and yes is not None:
            line = num(spec) if "total" in spec else 1.5
            over = mid == 58
            def both(m):
                t1, t2 = m.h1 + m.a1, m.h2 + m.a2
                return ((t1 > line and t2 > line) if over else (t1 < line and t2 < line)) == yes
            return both, scope
        if mid == 900027:
            yes = {"39": True, "40": False}.get(desc, yes_no(desc))
            if yes is None:
                return None
            return (lambda m: (m.h1 > 0 and m.a1 > 0 and m.h2 > 0 and m.a2 > 0) == yes), scope
        if mid == 1179:                 # 1st half result or match result
            want = RESULT.get(desc.lower())
            return (lambda m: res(m.h1, m.a1) == want or res(m.h, m.a) == want), scope
        if mid in (7, 900028, 900035):  # rest of match from the score in the spec
            s = re.search(r"score=(\d+):(\d+)", spec)
            if not s:
                return None
            sx, sy = int(s[1]), int(s[2])
            if mid == 7:
                want = RESULT.get(desc.lower())
                return (lambda m: res(m.h - sx, m.a - sy) == want), scope
            if mid == 900028:
                return (lambda m: over_under(desc, (m.h - sx) + (m.a - sy))), scope
            side = desc.lower().split(" ")[0]
            line = num(desc)
            if side == "home":
                return (lambda m: asian(m.h - sx, m.a - sy, line)), scope
            return (lambda m: asian(m.a - sy, m.h - sx, line)), scope
    except (ValueError, AttributeError, KeyError, TypeError, IndexError):
        return None
    return None


# ---- the model --------------------------------------------------------------------------------

def rates(stats):
    """Expected goals (home, away) from the last-10 records: each side's scoring averaged with the
    other side's conceding."""
    h, a = stats["home"], stats["away"]
    if not h.get("n") or not a.get("n"):
        return None
    lh = (h["avg_scored"] + a["avg_conceded"]) / 2
    la = (a["avg_scored"] + h["avg_conceded"]) / 2
    return round(max(lh, 0.05), 3), round(max(la, 0.05), 3)


def _poisson(rnd, lam):
    # Knuth: fine for the small means football has.
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= L:
            return k
        k += 1


def simulate(lh, la, n=SIMS, seed=7):
    """n simulated matches (as M), fixed seed so the same game always gets the same numbers."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        seq = []
        for half, share in ((1, FIRST_HALF_SHARE), (2, 1 - FIRST_HALF_SHARE)):
            goals = ["h"] * _poisson(rnd, lh * share) + ["a"] * _poisson(rnd, la * share)
            rnd.shuffle(goals)
            seq += [(half, t) for t in goals]
        out.append(M(seq))
    return out


def prob(r, sims):
    """Share of simulated matches the outcome wins (void is not a win)."""
    return sum(1 for m in sims if r(m) is True) / len(sims)


# ---- the live cushion -------------------------------------------------------------------------

def orders(h, a, cap=60):
    """Distinct orders of h home and a away goals (capped: a big score is never close to a bet)."""
    out = set()
    for pos in itertools.combinations(range(h + a), h):
        s = ["a"] * (h + a)
        for p in pos:
            s[p] = "h"
        out.add(tuple(s))
        if len(out) >= cap:
            break
    return out


def cushion(r, halves, half_now):
    """Fewest further goals (either side, any order) that lose the bet. halves: [(h1, a1), (h2, a2)] so
    far; half_now: 1 or 2, the half the further goals fall in. 0 = losing already."""
    pasts = []
    first = orders(*halves[0])
    second = orders(*halves[1]) if len(halves) > 1 else {()}
    for o1 in first:
        for o2 in second:
            pasts.append([(1, t) for t in o1] + [(2, t) for t in o2])
    worst = MAX_EXTRA + 1
    for past in pasts:
        if r(M(past)) is False:
            return 0
        for k in range(1, worst):
            if any(r(M(past + [(half_now, t) for t in extra])) is False
                   for extra in itertools.product("ha", repeat=k)):
                worst = k
                break
    return worst
