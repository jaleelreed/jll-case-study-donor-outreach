#!/usr/bin/env python3
"""
calculate_asks.py: deterministic segmentation and ask calculation for the
charity-donor-outreach skill.

Usage:
    python calculate_asks.py --csv donors.csv --config config.json --out ./outputs/

Inputs:
    donors.csv   Donor list per the schema in SKILL.md.
    config.json  Must include campaign_type and campaign_date (YYYY-MM-DD).

Outputs:
    <out>/asks.csv        One row per letter-eligible donor.
    <out>/exceptions.csv  Excluded records with reasons (suppressed, missing
                           fields, or unprocessable). Does NOT include
                           tier-mismatch or clamped-ask records -- those are
                           still processed and flagged in asks.csv instead.
    <out>/metrics.json    Run-level counts for performance monitoring:
                           total records, asks produced, exceptions, and the
                           rate of flagged (tier-mismatch/clamped/lapsed/etc.)
                           records. Intended to be logged per campaign so
                           degradation trips a visible threshold over time.

All coefficients are configuration, reviewed per campaign:
"""

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

# ---- Campaign coefficients (review before each campaign) -------------------
TIER_RATES = {"Platinum": 0.40, "Gold": 0.25, "Silver": 0.15}
TIER_BOUNDS = [("Platinum", 50_000), ("Gold", 10_000), ("Silver", 1_000), ("Bronze", 0)]
BRONZE_RATE = 1.25          # Bronze base = largest gift x 1.25
BRONZE_FLOOR = 75
LOYALTY_UPLIFT = 1.10       # gave in the calendar year before campaign_date
VOLUNTEER_FLAT = 100        # capped at VOLUNTEER_PCT of running amount
VOLUNTEER_PCT = 0.20
EMERGENCY_MULT = 1.20
LAPSED_MONTHS = 36
LAPSED_FLOOR = 50
GLOBAL_FLOOR = 25
CAP_VS_LARGEST = 1.50       # ask never exceeds 150% of largest gift
ROUND_TO = 25
# ---------------------------------------------------------------------------


def parse_history(raw):
    """'2019:500; 2020:750' -> [(2019, 500.0), (2020, 750.0)]"""
    gifts = []
    for part in (raw or "").replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        year_s, _, amount_s = part.partition(":")
        gifts.append((int(year_s.strip()), float(amount_s.strip().lstrip("$").replace(",", ""))))
    return gifts


def compute_tier(lifetime):
    for tier, floor in TIER_BOUNDS:
        if lifetime >= floor:
            return tier
    return "Bronze"


def truthy(v):
    return str(v or "").strip().lower() in {"true", "yes", "y", "1"}


def round_to(amount, step):
    return int(round(amount / step) * step)


def process(row, cfg, campaign_dt):
    flags = []
    gifts = parse_history(row.get("gift_history"))
    if not gifts:
        raise ValueError("missing or unparseable gift_history")

    largest = max(a for _, a in gifts)
    lifetime = sum(a for _, a in gifts)
    last_year = max(y for y, _ in gifts)

    tier = compute_tier(lifetime)
    stated = (row.get("tier") or "").strip().title()
    if stated and stated != tier:
        flags.append(f"tier_mismatch:stated={stated},computed={tier}")

    # Recency: months since Dec 31 of last gift year (conservative).
    months_since = (campaign_dt.year - last_year) * 12 + campaign_dt.month - 12
    lapsed = months_since > LAPSED_MONTHS

    # 1. Base
    if tier == "Bronze":
        ask = max(largest * BRONZE_RATE, BRONZE_FLOOR)
    else:
        ask = largest * TIER_RATES[tier]

    # 2. Loyalty uplift
    if last_year == campaign_dt.year - 1:
        ask *= LOYALTY_UPLIFT
        flags.append("loyalty_uplift")

    # 3. Volunteer uplift
    if truthy(row.get("volunteer")):
        ask += min(VOLUNTEER_FLAT, ask * VOLUNTEER_PCT)
        flags.append("volunteer_uplift")

    # 4. Emergency multiplier
    if cfg["campaign_type"] == "emergency":
        ask *= EMERGENCY_MULT

    # 5. Lapsed re-entry adjustment
    if lapsed:
        ask = max(min(ask, largest), LAPSED_FLOOR)
        flags.append("lapsed_reentry_ask")

    # 6. Round last, then clamp
    ask = round_to(ask, ROUND_TO)
    cap = round_to(largest * CAP_VS_LARGEST, ROUND_TO)
    if ask > cap:
        ask = cap
        flags.append("clamped_at_cap")
    if ask < GLOBAL_FLOOR:
        ask = GLOBAL_FLOOR
        flags.append("clamped_at_floor")

    return {
        "donor_id": row["donor_id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "title": (row.get("title") or "").strip(),
        "value_tier": tier,
        "recency_status": "Lapsed" if lapsed else "Active",
        "largest_gift": f"{largest:.2f}",
        "lifetime_total": f"{lifetime:.2f}",
        "last_gift_year": last_year,
        "ask_amount": ask,
        "applied_modifiers": ";".join(flags) or "none",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", default="./outputs/")
    args = p.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    for key in ("campaign_type", "campaign_date"):
        if key not in cfg:
            sys.exit(f"config missing required field: {key}")
    campaign_dt = date.fromisoformat(cfg["campaign_date"])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    asks, exceptions = [], []

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
        for i, row in enumerate(reader, start=2):
            ident = row.get("donor_id") or f"row_{i}"
            if truthy(row.get("deceased")):
                exceptions.append({"donor_id": ident, "reason": "suppressed:deceased"})
                continue
            if truthy(row.get("do_not_contact")):
                exceptions.append({"donor_id": ident, "reason": "suppressed:do_not_contact"})
                continue
            missing = [c for c in ("donor_id", "first_name", "last_name", "gift_history") if not (row.get(c) or "").strip()]
            if missing:
                exceptions.append({"donor_id": ident, "reason": f"missing_fields:{','.join(missing)}"})
                continue
            try:
                asks.append(process(row, cfg, campaign_dt))
            except (ValueError, KeyError) as e:
                exceptions.append({"donor_id": ident, "reason": f"unprocessable:{e}"})

    with open(out / "asks.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asks[0].keys()) if asks else ["donor_id"])
        w.writeheader()
        w.writerows(asks)

    with open(out / "exceptions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["donor_id", "reason"])
        w.writeheader()
        w.writerows(exceptions)

    flagged = [a for a in asks if a["applied_modifiers"] != "none"]
    total_records = len(asks) + len(exceptions)
    metrics = {
        "campaign_type": cfg["campaign_type"],
        "campaign_date": cfg["campaign_date"],
        "total_records": total_records,
        "asks_produced": len(asks),
        "exceptions": len(exceptions),
        "exception_rate": round(len(exceptions) / total_records, 4) if total_records else 0.0,
        "flagged_asks": len(flagged),
        "flag_rate": round(len(flagged) / len(asks), 4) if asks else 0.0,
        "flag_breakdown": {},
    }
    for a in flagged:
        for flag in a["applied_modifiers"].split(";"):
            key = flag.split(":", 1)[0]
            metrics["flag_breakdown"][key] = metrics["flag_breakdown"].get(key, 0) + 1

    with open(out / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"asks: {len(asks)} written to {out/'asks.csv'}")
    print(f"exceptions: {len(exceptions)} written to {out/'exceptions.csv'}")
    print(f"metrics: written to {out/'metrics.json'}")


if __name__ == "__main__":
    main()
