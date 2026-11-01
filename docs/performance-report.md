# Performance report (PR 9, book Ch 11)

## The bottleneck

**The transfer API's worker: one hot wallet, one thread, one transfer at a time.**
Under the nightly load (40 users, 3 minutes) the chain accepted **51.6 transactions/s**
from per-user accounts. The API's worker confirmed **12.9 transfers/s**, and that was
*after* this PR's fixes; before them it was **4.0/s**. The indexer kept up throughout:
its lag stayed at **p95 1 block, max 2**.

Everything below is measured by `tests/load/run_load.py` (Locust plus the SUT's
sampler) and `tests/load/profile_worker.py` (cProfile, plus counts of JSON-RPC calls
and SQL statements). The raw evidence is in [`docs/performance/`](performance/).

## Setup

| | |
|---|---|
| Chain | Anvil, 1-second interval mining during load (automine for setup only) |
| SUT process | transfer API (uvicorn) + its worker thread (`tick()` every 0.2 s) + indexer loop (0.25 s) + sampler (0.5 s) |
| Load process | Locust 2.47, 40 users, spawn 5/s, 180 s. Weights: `ApiWriteUser` 3, `ApiReadUser` 3, `ChainWriteUser` 2, `ChainReadUser` 2 |
| Per-user accounts | each `ChainWriteUser` owns one funded account and its own nonce (no shared sender, F-11) |
| Database | SQLite file (local runs below); Postgres 16 in the nightly CI job |
| Gate | after the load, every accepted transfer final; the API total equals what the hot wallet paid on chain; the indexer reconciles (LOAD-D1, LOAD-D2) |

Absolute numbers are from one cloud-session machine; shared CI runners differ. The
*ratios* between the API and the chain, and between before and after, are the
result.

## Before and after

| Metric | Before (`main` @ PR 8) | After (this PR) |
|---|---|---|
| **Correctness gate** | **FAIL**: 5,153 transfers still `submitted` after a 120 s drain | **PASS**: 2,862 of 2,862 confirmed, drained in 17.8 s |
| API transfers created | 6,039 (33.5/s, unbounded) | 2,862 (15.9/s) + 977 requests refused with 503 and retried |
| API transfers confirmed during load | **4.0/s** | **12.9/s** (3.2×) |
| Backlog (accepted, not final) | max 5,278, still growing at the end | max 509 (limit 500) |
| `POST /transfers` p50 / p95 | 160 / 650 ms | 120 / 530 ms |
| `GET /transfers/{id}` p50 / p95 | 43 / 130 ms | 32 / 120 ms |
| Direct `eth_sendRawTransaction` (per-user accounts) | 51.0/s, p95 43 ms | 51.6/s, p95 41 ms |
| `eth_call balanceOf` | 48.8/s, p95 82 ms | 50.0/s, p95 72 ms |
| Indexer lag (blocks) p50 / p95 / max | 0 / 1 / 2 | 0 / 1 / 2 |
| Full-balance query from the indexer DB | 0.7 → **162 ms** over 15,221 events | 0.8 → 77 ms over 12,180 events |
| Request failures | 0 | 0 |

Full reports: [`nightly-before.md`](performance/nightly-before.md) and
[`nightly-after.md`](performance/nightly-after.md), with the JSON next to them.

**On Postgres** (the nightly job's database) the after-fix run also passes: 2,306 of
2,306 confirmed, **10.0 confirmed/s** during load, 993 requests refused with 503,
backlog max 508, drain 20.3 s, indexer lag max 2. See
[`nightly-after-postgres.md`](performance/nightly-after-postgres.md). It is somewhat
slower than SQLite here because each of the worker's 5 commits per transfer is a
network round trip to the server.

## Why the worker, and where its time goes

`profile_worker.py` puts 200 accepted transfers through a fresh worker with automine,
so that mining is never what it waits for. Raw output after the fixes is in
[`worker-profile-after.txt`](performance/worker-profile-after.txt).

| Per transfer | Before | After |
|---|---|---|
| Throughput, worker alone | 20.9/s | 27.7/s |
| JSON-RPC calls | **8.0** (5.0 `eth_blockNumber`, 1 `getBlock`, 1 send, 1 receipt) | **2.1** (1 send, 1 receipt, 0.1 `getBlock`) |
| Time in JSON-RPC | 15.5 ms | 4.0 ms |
| SQL statements / commits | 18 / 5 | 18 / 5 |
| Time in commits | 7.4 ms | 6.9 ms |
| Signing (`build_transaction` + ECDSA, pure Python) | 13.6 ms | 11.5 ms |

Three things make the worker the bottleneck:

1. **It is serial by design.** One hot wallet has one nonce stream (TAPI-D5), and
   each transfer's write-ahead attempt must be committed before it is broadcast
   (TAPI-D7). After the fixes, about 36 ms of strictly ordered work remains per
   transfer.
2. **It shares a process with the API.** On its own it does 27.7/s; next to 40 users'
   HTTP traffic it does 12.9/s. Signing and SQLAlchemy are CPU-bound Python, so the
   worker and the API's request threads compete for the GIL.
3. **The chain is not the limit.** In the same run, per-user accounts pushed 51.6
   transactions/s through the node, with p95 41 ms. The hot wallet uses about a
   quarter of that.

## What was fixed in this PR

- **F-23, head-of-line blocking and an unbounded backlog.** A `tick()` submitted the
  *whole* accepted backlog before tracking any receipt. With 3,500 queued, one tick
  ran for minutes, and confirmations stood still: 140 confirmed from t = 65 s to
  t = 130 s.
  - Fix: a bounded batch per tick (TAPI-D8, TAPI-18).
  - Fix: backpressure. Over 500 unsettled transfers, `POST` answers 503 with
    `Retry-After`. Clients retry with the same idempotency key (TAPI-17).
- **F-24, bookkeeping RPC calls.** 6 of 8 calls per transfer only read the block
  number or base fee for history rows. Fix: one snapshot of the chain per tick
  phase, refreshed after any refused send, so a fee spike is still seen (TAPI-15
  still passes).

## Not fixed here, ranked by expected gain

1. **Shard by hot wallet.** N wallets, each with its own worker and nonce stream,
   scale almost linearly until the chain's limit (about 51/s here). TAPI-D5 already
   requires one process per wallet, so sharding is the supported way to scale.
2. **Run the worker in its own process.** That takes it out of the API's GIL
   contention. Measured gap: 27.7/s alone, 12.9/s alongside the API.
3. **Fewer commits per transfer.** Only the write-ahead commit must precede the
   broadcast. The `submitted` / `mined` / `confirmed` transitions could be batched
   per tick: 5 commits down to about 2.
4. **The indexer's full-balance query is O(events)** (IDX-D4, F-25). It's 77–162 ms at
   12–15k events, fine for a nightly reconciliation, not for an API read path. A
   materialised balance table would have to be rebuilt from the common ancestor on a
   reorg.

## How to reproduce

```bash
.venv/bin/python tests/load/run_load.py --profile nightly --out load-results   # report in load-results/report.md
.venv/bin/python tests/load/profile_worker.py --transfers 200
```

The `nightly` workflow runs the first command against Postgres at 03:17 UTC, writes the
report into the job summary and uploads `load-results/`.
