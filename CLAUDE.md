# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose and layout

Goal: download the full transaction history of the user's Millennium bcp accounts via the **B2B Inquiring API**. Python, stdlib only (no dependencies, no test suite, no git repo yet).

- `b2b-inquiry-v1-mock.yaml`: OpenAPI 3.0.1 spec; the source of truth for request/response shapes.
- `.env` (template in `.env.example`, documented in `README.md`): `URL` (API base URL), `API_KEY` (subscription key), optionally `ACCOUNTS` (comma-separated BBANs). Never print, log or commit `API_KEY`. Keep `.env.example` and the README tables in sync when adding variables or CLI options.
- Environments: `MBCP_ENV=prod` loads `.env.prod` (hard error if missing, no fallback to `.env`), and the default output dir becomes `exports-prod/`. Don't run anything against `MBCP_ENV=prod` without the user asking, because it hits the real bank with real credentials.
- `mbcp_client.py`: `MbcpClient` (loads `.env`, builds the `RqUID`/`MsgRqHdr` envelope, raises `ApiError` on HTTP errors). Run directly to print balances.
- `download_transactions.py`: history downloader, writing `exports/<account>/transactions.{json,csv}`.

```sh
python3 mbcp_client.py <acct> [...]                  # balances ("list accounts")
python3 download_transactions.py <acct> [...]        # full history
python3 download_transactions.py <acct> --since 2020-01-01 --stop-after 6 --split-at 500
```

## Key constraints behind the downloader design

- **No login and no list-accounts endpoint.** Auth is only the subscription key; every call needs the account id up front.
- **No paging**, only an optional `RecordCountLimit.MaxRecLimit`, and the transactions endpoint takes exactly one `AcctTrnSel` per request. So the downloader walks backwards in calendar-month `DtRange` windows. It stops after `--stop-after` consecutive months with no *new* transactions (taken as the start of the history) or at `--since`. With `--split-at N`, it bisects any window returning ≥ N records on the assumption that it was truncated.
- Transactions have no unique id field (`AcctTrnId` is the **account** id). They are deduplicated on `XferId + PostedDt + Amt + StmtRunningBal + Name` (`txn_key`). Output is merged with what's already on disk, so re-runs resume.
- **The `v1-mock` URL is a static mock.** It returns the spec's example response regardless of input (any account id, any date range, even an empty body) and echoes the example's `RqUID`. The date-window stopping, bisection and real history depth can't be verified until the bank provides the non-mock URL and key. The downloader detects "server ignores DtRange" and doesn't bisect in that case.

## Calling the API

- Base URL: `https://apiqa.millenniumbcp.pt/b2bInquiring/v1-mock` (same as `URL` in `.env`).
- Auth: an API Management subscription key, sent either as the `Ocp-Apim-Subscription-Key` header (preferred) or the `subscription-key` query parameter.
- Every operation is a `POST` with a JSON body, including the read-only inquiries.

```sh
set -a; source .env; set +a
curl -s -X POST "$URL/services/accounts/balances/inquiry" \
  -H "Ocp-Apim-Subscription-Key: $API_KEY" -H "Content-Type: application/json" \
  -d @request.json
```

## Endpoints → message envelopes

The API follows **IFX-style** naming (`Acct`, `Trn`, `Xfer`, `CurAmt`, `RqUID`, …). Each body is wrapped in a named root object; the request/response roots per endpoint:

| Path | operationId | Request root | Response root |
|---|---|---|---|
| `/services/accounts/balances/inquiry` | AccountsBalancesInquiry | `AcctInqRq` | `AcctInqRs` |
| `/services/accounts/transactions/inquiry` | AccountTransactionsInquiry | `AcctTrnInqRq` | `AcctTrnInqRs` |
| `/services/parties/payee-information/inquiry` | AccountPayeeInformationInquiry | `AcctInqRq` | `AcctInqRs` |
| `/services/parties/confirmation-of-payee/verify` | ConfirmationOfPayeeBulkInquiry | `PartyAcctRelInqRq` | `PartyAcctRelInqRs` |
| `/services/payments/transactions/return-reasons/inquiry` | TransactionReturnReasonsInquiry | `AcctTrnInqRq` | `AcctTrnInqRs` (some examples use `XferAddRs`) |
| `/services/accounts/transfer-available-modes/inquiry` | TransferAvailableModesInquiry | `XferAvailableModesRqMsg.XferAvailableModesInqRq` | `XferAvailableModesRsMsg.XferAvailableModesInqRs` |

Note that root names are reused across endpoints with different inner shapes, so typed clients should model each endpoint's schema (`components/schemas/<Operation>Request|Response|ErrorResponse`) rather than sharing types by root name.

## Shared message conventions

- **Request header**: `RqUID` (UUID, echoed back in the response) and `MsgRqHdr.ContextRqHdr` (`ClientDt` ISO-8601 with offset, `CustLangPref` e.g. `PT`, `ClientApp{Org, Name, Version}`).
- **Account keys**: `AcctKeys{AcctIdent{AcctIdentType: BBAN, AcctIdentValue}, AcctType: DDA, CurCode{CurCodeValue: EUR}}`. Transaction queries add `DtRange{StartDt, EndDt}`.
- **Response status**: `Status{StatusCode, StatusDesc, Severity, AdditionalStatus[]}`, plus `MsgRsHdr.ContextRsHdr.ServerDt`. `StatusCode: 0` / `Severity: Info` means success. Seen error codes: `1020` (required element missing; `SubjectElement` gives the JSON path), `100` (generic failure), and `500`/`502` inside `AdditionalStatus` for backend errors. HTTP 400/500/502 bodies use the same envelope, so check `Status` and not only the HTTP code.
- Per-record status lives on the record itself (`AcctStatus` / `AcctTrnStatus`, with `AcctStatusCode: Success|…`).
- Amounts are `CurAmt{Amt (number, debits negative), CurCode{CurCodeValue}}`. Balances come as `AcctBal[]` keyed by `BalType` (`Ledger`, `Avail`, `Current`).
- Server error text can be in Portuguese (e.g. `Conta não pertence ao Patrimonio.`).
