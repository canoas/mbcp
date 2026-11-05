# Millennium bcp transaction export

Downloads the full transaction history of Millennium bcp accounts through the B2B Inquiring API (spec: `b2b-inquiry-v1-mock.yaml`). Requires Python 3.9+, no extra packages.

## Configuration

```sh
cp .env.example .env   # then edit it
```

| Variable | Required | Description |
|---|---|---|
| `URL` | yes | API base URL, no trailing slash. The QA mock is `https://apiqa.millenniumbcp.pt/b2bInquiring/v1-mock`. |
| `API_KEY` | yes | Subscription key, sent as the `Ocp-Apim-Subscription-Key` header. There is no other login. |
| `ACCOUNTS` | no | Comma-separated account numbers (BBAN). Used when no accounts are passed on the command line. |

Variables already set in the shell take precedence over `.env`.

### Environments

`.env` is the default (the QA mock). For production, create `.env.prod` from the same template and select it with `MBCP_ENV`:

```sh
cp .env.example .env.prod    # set the real URL and API_KEY
chmod 600 .env.prod
MBCP_ENV=prod python3 download_transactions.py
```

`MBCP_ENV=<name>` loads `.env.<name>` and fails if it doesn't exist. It never falls back to `.env`, so a typo can't silently hit the wrong environment. Exports go to `exports-<name>/` instead of `exports/`, so mock data never mixes with real data. `.gitignore` excludes all `.env*` files except `.env.example`, as well as all export folders.

There is no endpoint that lists your accounts, so you must supply the account numbers yourself (from online banking or your contract).

> The `v1-mock` URL returns the spec's example data whatever you send (any account number, any dates). Use the real URL and key from the bank to export real history.

## Usage

Check access and show balances:

```sh
$ python3 mbcp_client.py 1372742
1372742  [Success]  EUR  Ledger=482796.36  Avail=482796.36  Current=482796.36
```

Download the full history:

```sh
$ python3 download_transactions.py 1372742
1372742: 0 already on disk, fetching 2000-01-01 .. 2026-11-05
  2026-11-01 .. 2026-11-05: 12 returned, 12 new
  2026-10-01 .. 2026-11-01: 12 returned, 0 new
  ...
  12 consecutive windows with nothing new; assuming start of history
  saved 12 transactions to exports/1372742/
```

| Option | Default | Description |
|---|---|---|
| `--since DATE` | `2000-01-01` | Earliest date to request. |
| `--until DATE` | now | Latest date to request. |
| `--stop-after N` | `12` | Stop after N consecutive months with no new transactions (`0` = only stop at `--since`). |
| `--split-at N` | off | Treat a month returning ≥ N records as truncated and split it in half until each piece fits. |
| `--out DIR` | `exports` (`exports-<MBCP_ENV>` when set) | Output directory. |

Re-running merges with what is already on disk, so it only adds new transactions.

## Output

`exports/<account>/transactions.json` holds the raw API records, sorted by posting date. `exports/<account>/transactions.csv` holds the same records flattened:

```csv
account,posted_dt,eff_dt,type,sub_type,amount,currency,running_balance,name,xfer_id,ref_ident,bill_ref,status
1372742,2026-06-01T00:00:00.814Z,2026-06-01T00:00:00.814Z,Debit,91,-50,EUR,487685.84,DD OCIDENTAL 00013610162 VIDA        RKA0132986,2026052599SRSSBB0004,09985,1:129991-38-84-1-0-1,Success
```

Debits have negative amounts. `running_balance` is the account balance after the transaction.
