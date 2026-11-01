"""Start and stop a local Anvil node for the test session."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

from web3 import Web3

#: Anvil's default dev mnemonic. Its keys are public knowledge: never fund them on a
#: real network.
DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"
CHAIN_ID = 31337


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Anvil:
    """A running Anvil subprocess bound to a free local port."""

    process: subprocess.Popen
    url: str

    @classmethod
    def start(cls, *, accounts: int = 10, timeout: float = 15.0) -> Anvil:
        binary = shutil.which("anvil") or os.path.expanduser("~/.foundry/bin/anvil")
        port = _free_port()
        process = subprocess.Popen(
            [
                binary,
                "--host", "127.0.0.1",
                "--port", str(port),
                "--chain-id", str(CHAIN_ID),
                "--accounts", str(accounts),
                "--mnemonic", DEFAULT_MNEMONIC,
                "--silent",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        anvil = cls(process=process, url=f"http://127.0.0.1:{port}")
        anvil._wait_until_ready(timeout)
        return anvil

    def _wait_until_ready(self, timeout: float) -> None:
        """Poll until the node answers eth_chainId, or fail with its stderr."""
        w3 = Web3(Web3.HTTPProvider(self.url, request_kwargs={"timeout": 2}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                err = self.process.stderr.read().decode() if self.process.stderr else ""
                raise RuntimeError(f"anvil exited early ({self.process.returncode}): {err}")
            try:
                if w3.eth.chain_id == CHAIN_ID:
                    return
            except Exception:  # connection refused while it boots
                time.sleep(0.1)
        self.stop()
        raise TimeoutError(f"anvil did not become ready on {self.url} within {timeout}s")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
