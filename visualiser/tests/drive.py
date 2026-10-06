"""Drive real PRT activity on an Anvil loaded with the stack's state.json.

Uses Anvil's unlocked accounts (eth_sendTransaction), so no keys are needed.
Sybil commitments are built so that joinTournament's last-leaf proof holds.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vis import abi  # noqa: E402
from vis.eth import Eth, RpcError  # noqa: E402
from vis.keccak import keccak256  # noqa: E402

APP = "0x6c2e2f9665b8f941aa8d94ea3f0287f7884a6146"
INPUT_BOX = "0xebe9f4dfc04ae10bbee663859c3dc5a23f94ea3c"
ACCOUNTS = {
    0: "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    1: "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    2: "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    3: "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
    4: "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65",
    6: "0x976ea74026e726554db657fa54763abd0c3a0aa9",
    7: "0x14dc79964da2c08b23698b3d3cc7ca32193d9955",
}


def send(eth: Eth, sender: str, to: str, data: str, value: int = 0, expect_revert: bool = False):
    tx = {"from": sender, "to": to, "data": data, "value": hex(value), "gas": hex(3_000_000)}
    try:
        tx_hash = eth.call("eth_sendTransaction", [tx])
    except RpcError as exc:
        if expect_revert:
            return None
        raise
    receipt = eth.call("eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        eth.call("evm_mine")
        receipt = eth.call("eth_getTransactionReceipt", [tx_hash])
    if receipt["status"] != "0x1" and not expect_revert:
        raise RuntimeError(f"tx reverted: {tx_hash}")
    return tx_hash


def add_input(eth: Eth, text: str, sender: int = 1) -> str:
    payload = text.encode().hex()
    length = len(text.encode())
    padded = payload.ljust(((len(payload) + 63) // 64) * 64, "0") or ""
    data = (abi.selector("addInput(address,bytes)") + APP[2:].rjust(64, "0")
            + f"{64:064x}" + f"{length:064x}" + padded)
    return send(eth, ACCOUNTS[sender], INPUT_BOX, data)


def sybil_commitment(height: int, final_state: bytes, salt: bytes):
    siblings = [keccak256(salt + i.to_bytes(4, "big")) for i in range(height)]
    node = final_state
    for sibling in siblings[:-1]:
        node = keccak256(sibling + node)
    left, right = siblings[-1], node
    root = keccak256(left + right)
    return siblings, left, right, root


def join(eth: Eth, tournament: str, sender: int, final_state: bytes, salt: bytes,
         expect_revert: bool = False):
    desc = abi.decode(["bytes32", "uint256", "uint64", "uint64", "uint64", "uint8", "uint64", "uint64"],
                      eth.eth_call(tournament, abi.encode_call("tournamentDescriptor()")))
    height = desc[3]
    bond = abi.decode(["uint256"], eth.eth_call(tournament, abi.encode_call("bondValue()")))[0]
    siblings, left, right, root = sybil_commitment(height, final_state, salt)
    data = (abi.selector("joinTournament(bytes32,bytes32[],bytes32,bytes32)")
            + final_state.hex() + f"{128:064x}" + left.hex() + right.hex()
            + f"{len(siblings):064x}" + "".join(s.hex() for s in siblings))
    tx = send(eth, ACCOUNTS[sender], tournament, data, value=bond, expect_revert=expect_revert)
    return tx, "0x" + root.hex()


def current_tournament(eth: Eth) -> str:
    consensus = abi.decode(["address"], eth.eth_call(APP, abi.encode_call("getOutputsMerkleRootValidator()")))[0]
    sealed = abi.decode(["uint256", "uint256", "uint256", "address"],
                        eth.eth_call(consensus, abi.encode_call("getCurrentSealedEpoch()")))
    return sealed[3]


def scenario(eth: Eth) -> dict:
    """Inputs, a two-sybil dispute, and one reverted duplicate join."""
    for text in ("hello from the harness", "<img src=x onerror=alert(1)>", "third input"):
        add_input(eth, text)
    tournament = current_tournament(eth)
    state_a = keccak256(b"sybil a final state")
    state_b = keccak256(b"sybil b final state")
    _, root_a = join(eth, tournament, 2, state_a, b"a")
    _, root_b = join(eth, tournament, 3, state_b, b"b")
    # Same commitment again from another account: must revert with ClockAlreadyInitialized.
    join(eth, tournament, 4, state_a, b"a", expect_revert=True)
    eth.call("anvil_mine", [hex(3)])
    return {"tournament": tournament, "roots": [root_a, root_b]}


if __name__ == "__main__":
    rpc = Eth(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8545")
    print(scenario(rpc))


def win_by_timeout(eth: Eth, tournament: str, sender: int, one: str, two: str,
                   final_state: bytes, salt: bytes) -> str:
    """The side whose clock is still banked claims the match after the other runs out."""
    desc = abi.decode(["bytes32", "uint256", "uint64", "uint64", "uint64", "uint8", "uint64", "uint64"],
                      eth.eth_call(tournament, abi.encode_call("tournamentDescriptor()")))
    _, left, right, _ = sybil_commitment(desc[3], final_state, salt)
    data = (abi.selector("winMatchByTimeout((bytes32,bytes32),bytes32,bytes32)")
            + one[2:] + two[2:] + left.hex() + right.hex())
    return send(eth, ACCOUNTS[sender], tournament, data)
