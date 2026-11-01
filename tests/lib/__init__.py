"""Shared integration-test library: Anvil, artifacts, service objects, builders.

Everything here talks to a real Anvil node through web3.py and deploys the
contracts from Foundry's `contracts/out/` artifacts, so the ABI and bytecode under
test are exactly what `forge build` produced.
"""
