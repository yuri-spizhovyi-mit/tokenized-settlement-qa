# tokenized-settlement-qa

A small tokenised settlement system (`SettlementToken` + `Escrow`) whose real
product is its test suite. It's a portfolio project for a Digital Asset QA
Engineer role, and a companion to *Testing the Chain*.

- Roadmap and milestones: [`docs/plan.md`](docs/plan.md)
- Behaviour decisions and the tests that pin them: [`docs/decisions.md`](docs/decisions.md)
- Contributor and agent rules: [`CLAUDE.md`](CLAUDE.md)

The full README (architecture, findings, badges) lands with PR 12.

## Run the tests

You need [Foundry](https://getfoundry.sh) **v1.8.7**, the version pinned in CI:

```bash
foundryup --install v1.8.7
git submodule update --init
cd contracts
forge build --sizes
forge test -vvv
forge snapshot --check
```

> **The Foundry version matters.** `contracts/.gas-snapshot` records gas per test
> and is checked in CI. Gas accounting differs between forge releases. With
> forge v1.7.1 or older, `forge snapshot --check` fails on 91 of 92 entries even
> though every test passes. Use v1.8.7, or regenerate the snapshot only as part
> of a deliberate Foundry upgrade.

In a Claude cloud session, where `foundryup` is blocked, run
`bash scripts/cloud-setup.sh` instead. It installs the same pinned version from
GitHub release binaries.
