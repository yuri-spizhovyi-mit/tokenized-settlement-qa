# Load run: nightly (40 users, 180 s, postgres)

## Correctness after load

| Check | Result |
|---|---|
| all_transfers_final | PASS |
| api_total_matches_chain | PASS |
| indexer_reconciles | PASS |
| no_worker_errors | PASS |
| no_indexer_errors | PASS |
| no_request_failures | PASS |

Transfers by status: `{'confirmed': 2306}`; drain took 20.3 s; gap fills: 0; indexed events: 11581; reconciled at block 290.

## Requests

| Type | Name | Count | Failures | req/s | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| backpressure | 503 Retry-After | 993 | 0 | 5.5 | 0 | 0 | 0 |
| GET | GET /health | 2 | 0 | 0.0 | 41 | 41 | 41 |
| GET | GET /transfers/{id} | 11526 | 0 | 64.0 | 39 | 140 | 200 |
| POST | POST /transfers | 3287 | 0 | 18.3 | 220 | 440 | 520 |
| rpc | eth_call balanceOf | 8857 | 0 | 49.2 | 21 | 79 | 110 |
| rpc | eth_sendRawTransaction | 9238 | 0 | 51.3 | 12 | 43 | 63 |

## Pipeline

| Metric | Value |
|---|---|
| API transfers created (202) / refused with 503 | 2306 / 993 |
| API transfers accepted / s | 12.8 |
| API transfers confirmed / s (during load) | 10.0 |
| Direct RPC sends / s (per-user accounts) | 51.3 |
| Worker busy fraction | 0.89 |
| Transfer backlog (not final): max / at end of load | 508 / 497 |
| Indexer lag, blocks: p50 / p95 / max | 0 / 1 / 2 |
| Full-balance query, ms: first / median / last | 4.5 / 81.06 / 79.71 |

**Overall: PASS**
