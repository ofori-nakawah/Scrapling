"""Lanes: every GHS 100 is a lane that rolls its winnings into its next stake (the user, 2026-10-16).

- A lane stakes its whole current amount on one slip at a time; a new slip goes to the first free lane.
- A win: the lane's amount becomes the payout. At SPLIT (200) or more the money has doubled, so the lane goes
  back to START (100), a new lane of START starts beside it, and anything above SPLIT goes to the reserve.
- A loss: the lane closes. A replacement of START comes from the reserve if it holds START, else from spare
  balance (money in the account not already set aside for a lane or the reserve), else it waits until one can.
- The reserve funds a new lane whenever it reaches START.
State lives in a JSON file; every change is written straight away, with a log line the monitor reports.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class Lanes:
    def __init__(self, path: Path, start: float = 100.0, split: float = 200.0):
        self.path, self.start, self.split = Path(path), float(start), float(split)
        try:
            self.d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.d = {"lanes": [], "closed": [], "reserve": 0.0, "owed": 0, "seq": 0}

    # ---- state ---------------------------------------------------------------------------------
    def save(self):
        self.path.write_text(json.dumps(self.d, indent=1))

    def _name(self) -> str:
        n, s = self.d["seq"], ""
        self.d["seq"] += 1
        while True:                       # A..Z, AA, AB ...
            s = chr(65 + n % 26) + s
            n = n // 26 - 1
            if n < 0:
                return s

    def new_lane(self, amount: float, why: str, open_code: str | None = None) -> dict:
        lane = {"id": self._name(), "amount": round(amount, 2), "open": open_code, "wins": 0,
                "created": int(time.time()), "why": why, "history": []}
        self.d["lanes"].append(lane)
        return lane

    @property
    def lanes(self):
        return self.d["lanes"]

    def free(self):
        return next((l for l in self.lanes if not l["open"]), None)

    def by_code(self, code):
        return next((l for l in self.lanes if l["open"] == code), None)

    def set_aside(self) -> float:
        """Money in free lanes plus the reserve: not available as spare balance."""
        return round(sum(l["amount"] for l in self.lanes if not l["open"]) + self.d["reserve"], 2)

    # ---- events --------------------------------------------------------------------------------
    def assign(self, lane_id: str, code: str):
        for l in self.lanes:
            if l["id"] == lane_id:
                l["open"] = code
        self.save()

    def settle(self, code: str, won: bool, payout: float) -> list[str]:
        """A lane's slip has settled. Returns what happened, for the log and Telegram."""
        lane = self.by_code(code)
        if not lane:
            return []
        stake = lane["amount"]
        lane["history"].append({"code": code, "stake": stake, "won": won, "payout": round(payout, 2), "at": int(time.time())})
        out = []
        if won:
            lane["amount"], lane["open"] = round(payout, 2), None
            lane["wins"] += 1
            out.append(f"Lane {lane['id']} won: {stake:.2f} → {lane['amount']:.2f} ({lane['wins']} in a row)")
            if lane["amount"] >= self.split:
                extra = round(lane["amount"] - self.split, 2)
                lane["amount"], lane["wins"] = self.start, 0
                twin = self.new_lane(self.start, f"split from {lane['id']}")
                self.d["reserve"] = round(self.d["reserve"] + extra, 2)
                out.append(f"Lane {lane['id']} doubled: back to {self.start:.0f}, new lane {twin['id']} of {self.start:.0f}"
                           + (f", {extra:.2f} to the reserve ({self.d['reserve']:.2f})" if extra else ""))
        else:
            self.lanes.remove(lane)
            self.d["closed"].append({**lane, "open": None, "closed": int(time.time())})
            self.d["owed"] += 1
            out.append(f"Lane {lane['id']} lost {stake:.2f}: closed")
        self.save()
        return out

    def replenish(self, balance: float | None) -> list[str]:
        """New lanes from the reserve (whenever it holds START) and, for lost lanes, from spare balance."""
        out = []
        while self.d["reserve"] >= self.start:
            self.d["reserve"] = round(self.d["reserve"] - self.start, 2)
            self.d["owed"] = max(0, self.d["owed"] - 1)
            l = self.new_lane(self.start, "from the reserve")
            out.append(f"New lane {l['id']} of {self.start:.0f} from the reserve ({self.d['reserve']:.2f} left)")
        if balance is not None:
            while self.d["owed"] > 0 and balance - self.set_aside() >= self.start:
                self.d["owed"] -= 1
                l = self.new_lane(self.start, "fresh from balance")
                out.append(f"New lane {l['id']} of {self.start:.0f} from spare balance (replacing a lost lane)")
        if out:
            self.save()
        return out

    def summary(self) -> str:
        parts = [f"{l['id']} {l['amount']:.2f}" + (f" (on {l['open']})" if l["open"] else " free") for l in self.lanes]
        return f"{len(self.lanes)} lanes: " + ", ".join(parts) + f" · reserve {self.d['reserve']:.2f}" + \
               (f" · {self.d['owed']} waiting for money" if self.d["owed"] else "")
