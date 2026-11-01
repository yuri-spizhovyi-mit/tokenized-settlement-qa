# Load run: nightly (40 users, 180 s, sqlite)

## Correctness after load

| Check | Result |
|---|---|
| all_transfers_final | PASS |
| api_total_matches_chain | PASS |
| indexer_reconciles | PASS |
| no_worker_errors | PASS |
| no_indexer_errors | PASS |
| no_request_failures | PASS |

Transfers by status: `{'confirmed': 2862}`; drain took 17.8 s; gap fills: 0; indexed events: 12180; reconciled at block 288.

## Requests

| Type | Name | Count | Failures | req/s | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| backpressure | 503 Retry-After | 977 | 0 | 5.4 | 0 | 0 | 0 |
| GET | GET /health | 2 | 0 | 0.0 | 36 | 36 | 36 |
| GET | GET /transfers/{id} | 12187 | 0 | 67.7 | 32 | 120 | 190 |
| POST | POST /transfers | 3827 | 0 | 21.2 | 120 | 530 | 950 |
| rpc | eth_call balanceOf | 9007 | 0 | 50.0 | 20 | 72 | 100 |
| rpc | eth_sendRawTransaction | 9281 | 0 | 51.5 | 12 | 41 | 61 |

## Pipeline

| Metric | Value |
|---|---|
| API transfers created (202) / refused with 503 | 2862 / 977 |
| API transfers accepted / s | 15.9 |
| API transfers confirmed / s (during load) | 12.9 |
| Direct RPC sends / s (per-user accounts) | 51.6 |
| Worker busy fraction | 0.86 |
| Transfer backlog (not final): max / at end of load | 509 / 508 |
| Indexer lag, blocks: p50 / p95 / max | 0 / 1 / 2 |
| Full-balance query, ms: first / median / last | 0.8 / 62.49 / 76.5 |

**Overall: PASS**
