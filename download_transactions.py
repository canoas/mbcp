"""Download the full transaction history for one or more accounts.

Walks backwards from --until in calendar-month windows until either --since is
reached or --stop-after consecutive windows yield no new transactions (i.e. we
are past the start of the available history). Results are merged into
exports/<account>/transactions.{json,csv}, so re-running only adds what's new.

The API has no paging. If a window returns at least --split-at records it is
assumed truncated and is bisected until each piece fits.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mbcp_client import ApiError, MbcpClient, env_name

CSV_FIELDS = [
    "account", "posted_dt", "eff_dt", "type", "sub_type", "amount", "currency",
    "running_balance", "name", "xfer_id", "ref_ident", "bill_ref", "status",
]


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "T" not in s else datetime.fromisoformat(s)


def month_windows(until, since):
    """Yield (start, end) month-aligned windows from `until` back to `since`."""
    end = until
    while end > since:
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start == end:  # end is exactly a month boundary: step to previous month
            start = (start - timedelta(days=1)).replace(day=1)
        yield max(start, since), end
        end = start


def txn_key(rec):
    info = rec.get("AcctTrnInfo", {})
    return "|".join(str(x) for x in (
        info.get("XferKeys", {}).get("XferId"),
        info.get("PostedDt"),
        info.get("TotalCurAmt", {}).get("Amt"),
        info.get("StmtRunningBal", {}).get("Amt"),
        info.get("Name"),
    ))


def to_row(acct, rec):
    info = rec.get("AcctTrnInfo", {})
    amt = info.get("TotalCurAmt", {})
    return {
        "account": acct,
        "posted_dt": info.get("PostedDt"),
        "eff_dt": info.get("EffDt"),
        "type": info.get("TrnType", {}).get("TrnTypeValue"),
        "sub_type": info.get("TrnSubType"),
        "amount": amt.get("Amt"),
        "currency": amt.get("CurCode", {}).get("CurCodeValue"),
        "running_balance": info.get("StmtRunningBal", {}).get("Amt"),
        "name": (info.get("Name") or "").strip(),
        "xfer_id": info.get("XferKeys", {}).get("XferId"),
        "ref_ident": info.get("RefData", {}).get("RefIdent"),
        "bill_ref": info.get("BillRefText"),
        "status": rec.get("AcctTrnStatus", {}).get("AcctStatusCode"),
    }


class Downloader:
    def __init__(self, client, split_at=None, retries=3, min_window=timedelta(hours=1)):
        self.client = client
        self.split_at = split_at
        self.retries = retries
        self.min_window = min_window

    def fetch(self, acct, start, end):
        for attempt in range(self.retries + 1):
            try:
                rs = self.client.transactions(acct, iso(start), iso(end), max_records=self.split_at)
                return rs.get("AcctTrnRec") or []
            except ApiError as e:
                if e.http_status < 500 or attempt == self.retries:
                    raise
                wait = 2 ** attempt
                print(f"    HTTP {e.http_status}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)

    def fetch_window(self, acct, start, end, recs=None):
        if recs is None:
            recs = self.fetch(acct, start, end)
        if not (self.split_at and len(recs) >= self.split_at and end - start > self.min_window):
            return recs
        mid = start + (end - start) / 2
        left, right = self.fetch(acct, start, mid), self.fetch(acct, mid, end)
        parent_keys = {txn_key(r) for r in recs}
        if {txn_key(r) for r in left} == parent_keys == {txn_key(r) for r in right}:
            # Same records for both halves: the server isn't filtering by date
            # (the mock behaves like this), so splitting further is pointless.
            print("    server ignores DtRange; not splitting", file=sys.stderr)
            return recs
        return self.fetch_window(acct, start, mid, left) + self.fetch_window(acct, mid, end, right)

    def download(self, acct, since, until, stop_after, known):
        empty_streak = 0
        for start, end in month_windows(until, since):
            recs = self.fetch_window(acct, start, end)
            new = 0
            for rec in recs:
                k = txn_key(rec)
                if k not in known:
                    known[k] = rec
                    new += 1
            print(f"  {start:%Y-%m-%d} .. {end:%Y-%m-%d}: {len(recs)} returned, {new} new")
            empty_streak = 0 if new else empty_streak + 1
            if stop_after and empty_streak >= stop_after:
                print(f"  {stop_after} consecutive windows with nothing new; assuming start of history")
                break
        return known


def load_existing(path):
    if not path.exists():
        return {}
    return {txn_key(r): r for r in json.loads(path.read_text())}


def save(acct, out_dir, known):
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = sorted(known.values(), key=lambda r: r.get("AcctTrnInfo", {}).get("PostedDt") or "")
    (out_dir / "transactions.json").write_text(json.dumps(recs, ensure_ascii=False, indent=2))
    with open(out_dir / "transactions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(to_row(acct, r) for r in recs)
    return len(recs)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accounts", nargs="*", help="account ids (BBAN); defaults to $ACCOUNTS (comma-separated)")
    p.add_argument("--since", default="2000-01-01", help="earliest date to request (default 2000-01-01)")
    p.add_argument("--until", help="latest date to request (default now)")
    p.add_argument("--stop-after", type=int, default=12,
                   help="stop after N consecutive months with no new transactions (0 = never; default 12)")
    p.add_argument("--split-at", type=int, default=None,
                   help="treat a window with >= N records as truncated and bisect it")
    # Separate directory per environment so mock data never merges into real exports.
    default_out = f"exports-{env_name()}" if env_name() else "exports"
    p.add_argument("--out", default=default_out, help=f"output directory (default {default_out}/)")
    args = p.parse_args()

    client = MbcpClient()
    accounts = args.accounts or [a for a in os.environ.get("ACCOUNTS", "").split(",") if a]
    if not accounts:
        p.error("no accounts given (pass them as arguments or set ACCOUNTS in .env)")
    since = parse_date(args.since)
    until = parse_date(args.until) if args.until else datetime.now(timezone.utc)

    downloader = Downloader(client, split_at=args.split_at)
    failed = False
    for acct in accounts:
        out_dir = Path(args.out) / acct
        known = load_existing(out_dir / "transactions.json")
        print(f"{acct}: {len(known)} already on disk, fetching {since:%Y-%m-%d} .. {until:%Y-%m-%d}")
        try:
            downloader.download(acct, since, until, args.stop_after, known)
        except ApiError as e:
            print(f"  error: {e}", file=sys.stderr)
            failed = True
        # Save whatever we have, even after an error, so a re-run resumes from it.
        total = save(acct, out_dir, known)
        print(f"  saved {total} transactions to {out_dir}/")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
