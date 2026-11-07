# Load run: nightly (40 users, 180 s, sqlite)

## Correctness after load

| Check | Result |
|---|---|
| all_transfers_final | **FAIL** |
| api_total_matches_chain | **FAIL** |
| indexer_reconciles | PASS |
| no_worker_errors | PASS |
| no_indexer_errors | PASS |
| no_request_failures | PASS |

Transfers by status: `{'accepted': 51, 'confirmed': 830, 'mined': 5, 'submitted': 5153}`; drain took 120.1 s; gap fills: 0; indexed events: 15221; reconciled at block 390.

## Requests

| Type | Name | Count | Failures | req/s | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| GET | GET /health | 2 | 0 | 0.0 | 46 | 46 | 46 |
| GET | GET /transfers/{id} | 11552 | 0 | 64.5 | 43 | 130 | 190 |
| POST | POST /transfers | 6002 | 0 | 33.5 | 160 | 650 | 1200 |
| rpc | eth_call balanceOf | 8742 | 0 | 48.8 | 23 | 82 | 110 |
| rpc | eth_sendRawTransaction | 9174 | 0 | 51.2 | 13 | 43 | 64 |

## Pipeline

| Metric | Value |
|---|---|
| API transfers created (202) / refused with 503 | 6039 / 0 |
| API transfers accepted / s | 33.5 |
| API transfers confirmed / s (during load) | 4.0 |
| Direct RPC sends / s (per-user accounts) | 51.0 |
| Worker busy fraction | 0.59 |
| Transfer backlog (not final): max / at end of load | 5278 / 5245 |
| Indexer lag, blocks: p50 / p95 / max | 0 / 1 / 2 |
| Full-balance query, ms: first / median / last | 0.68 / 84.00999999999999 / 161.52 |

**Overall: FAIL**
