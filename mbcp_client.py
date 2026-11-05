"""Minimal client for the Millennium bcp B2B Inquiring API (stdlib only).

Auth is an API Management subscription key (no login/session). See
b2b-inquiry-v1-mock.yaml for the full spec.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


def env_name():
    """Active environment: MBCP_ENV=prod selects .env.prod; unset means .env."""
    return os.environ.get("MBCP_ENV", "")


def load_env(path=None):
    if path is None:
        name = env_name()
        path = Path(__file__).with_name(f".env.{name}" if name else ".env")
        if name and not path.exists():
            sys.exit(f"MBCP_ENV={name} but {path.name} not found")
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class ApiError(Exception):
    def __init__(self, http_status, body):
        self.http_status = http_status
        self.body = body
        super().__init__(f"HTTP {http_status}: {json.dumps(body, ensure_ascii=False)}")


class MbcpClient:
    def __init__(self, base_url=None, api_key=None, app_name="mbcp-export", lang="PT"):
        load_env()
        self.base_url = (base_url or os.environ["URL"]).rstrip("/")
        self.api_key = api_key or os.environ["API_KEY"]
        self.app_name = app_name
        self.lang = lang

    def _header(self):
        return {
            "RqUID": str(uuid.uuid4()),
            "MsgRqHdr": {
                "ContextRqHdr": {
                    "ClientDt": datetime.now(timezone.utc).isoformat(),
                    "CustLangPref": self.lang,
                    "ClientApp": {"Org": "Millennium bcp", "Name": self.app_name, "Version": "1.0.0"},
                }
            },
        }

    def post(self, path, body):
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Ocp-Apim-Subscription-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode(errors="replace")
            raise ApiError(e.code, body) from None

    @staticmethod
    def acct_keys(acct_id, ident_type="BBAN", acct_type="DDA", currency="EUR"):
        return {
            "AcctIdent": {"AcctIdentType": ident_type, "AcctIdentValue": acct_id},
            "AcctType": acct_type,
            "CurCode": {"CurCodeValue": currency},
        }

    def balances(self, acct_ids, **key_opts):
        body = {"AcctInqRq": {**self._header(), "AcctSel": [{"AcctKeys": self.acct_keys(a, **key_opts)} for a in acct_ids]}}
        return self.post("/services/accounts/balances/inquiry", body)["AcctInqRs"]

    def transactions(self, acct_id, start_dt, end_dt, max_records=None, **key_opts):
        sel = {
            "AcctKeys": self.acct_keys(acct_id, **key_opts),
            "DtRange": {"StartDt": start_dt, "EndDt": end_dt},
        }
        if max_records:
            sel["RecordCountLimit"] = {"MaxRecLimit": str(max_records)}
        # The API accepts exactly one AcctTrnSel item per request.
        body = {"AcctTrnInqRq": {**self._header(), "AcctTrnSel": [sel]}}
        return self.post("/services/accounts/transactions/inquiry", body)["AcctTrnInqRs"]


def list_accounts(client, acct_ids):
    rs = client.balances(acct_ids)
    for rec in rs.get("AcctRec", []):
        info = rec.get("AcctInfo", {})
        bals = {b["BalType"]: b["CurAmt"] for b in info.get("AcctBal", [])}
        status = rec.get("AcctStatus", {}).get("AcctStatusCode")
        cur = next(iter(bals.values()), {}).get("CurCode", {}).get("CurCodeValue", "")
        line = "  ".join(f"{k}={v['Amt']}" for k, v in bals.items())
        print(f"{rec.get('AcctId')}  [{status}]  {cur}  {line}")
    return rs


if __name__ == "__main__":
    # No "list accounts" endpoint exists: account ids must be supplied.
    ids = sys.argv[1:] or os.environ.get("ACCOUNTS", "1372742").split(",")
    try:
        list_accounts(MbcpClient(), ids)
    except ApiError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
