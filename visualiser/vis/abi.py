"""Minimal ABI encode/decode for the static calls and events the visualiser reads."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .keccak import keccak_hex

ABI = json.loads((Path(__file__).parent / "abi.json").read_text())
EVENTS: dict = ABI["events"]
FUNCTIONS: dict = ABI["functions"]
ERRORS: dict = ABI["errors"]

REQUIRED_EVENTS = {
    "EpochSealed", "EpochStaged", "SentryClaim", "InputAdded", "CommitmentJoined",
    "MatchCreated", "MatchAdvanced", "LeafMatchSealed", "MatchDeleted",
    "NewInnerTournament", "BondRecovered", "PartialBondRefund", "DaveAppCreated",
}
TOPIC = {info["name"]: topic for topic, info in EVENTS.items() if info["name"] in REQUIRED_EVENTS}
missing = REQUIRED_EVENTS - set(TOPIC)
if missing:  # pragma: no cover - guards a stale abi.json
    raise RuntimeError(f"vis/abi.json lacks events {sorted(missing)}; rerun tools/gen_abi.py")

TOURNAMENT_EVENTS = [TOPIC[n] for n in (
    "CommitmentJoined", "MatchCreated", "MatchAdvanced", "LeafMatchSealed", "MatchDeleted",
    "NewInnerTournament", "BondRecovered", "PartialBondRefund")]
CONSENSUS_EVENTS = [TOPIC[n] for n in ("EpochSealed", "EpochStaged", "SentryClaim")]

EVM_ADVANCE = keccak_hex("EvmAdvance(uint256,address,address,uint256,uint256,uint256,uint256,bytes)")[:10]


@lru_cache(maxsize=None)
def selector(signature: str) -> str:
    return keccak_hex(signature)[:10]


def encode_call(signature: str, *args) -> str:
    """Encode a call whose arguments are all static (uint, address, bytes32, bool)."""
    out = selector(signature)
    for arg in args:
        if isinstance(arg, bool):
            out += f"{int(arg):064x}"
        elif isinstance(arg, int):
            out += f"{arg:064x}"
        else:
            text = str(arg).lower().removeprefix("0x")
            out += text.rjust(64, "0")
    return out


def words(data: str) -> list[str]:
    raw = data[2:] if data.startswith("0x") else data
    return [raw[i: i + 64] for i in range(0, len(raw), 64)]


def decode(types: list[str], data: str) -> list:
    """Decode a flat list of static types (structs are passed pre-flattened)."""
    parts = words(data)
    if len(parts) < len(types):
        raise ValueError(f"short return data: {len(parts)} words for {len(types)} values")
    return [_static(kind, word) for kind, word in zip(types, parts)]


def _static(kind: str, word: str):
    if kind == "address":
        return "0x" + word[-40:]
    if kind == "bool":
        return int(word, 16) != 0
    if kind.startswith("uint"):
        return int(word, 16)
    if kind.startswith("int"):
        value = int(word, 16)
        return value - (1 << 256) if value >> 255 else value
    return "0x" + word  # bytes32 and friends


def decode_log(log: dict) -> dict | None:
    """Decode a log into {name, args}; returns None for events we do not know."""
    topics = log.get("topics") or []
    if not topics:
        return None
    info = EVENTS.get(topics[0].lower())
    if not info:
        return None
    indexed = [p for p in info["inputs"] if p["indexed"]]
    plain = [p for p in info["inputs"] if not p["indexed"]]
    args: dict = {}
    for param, topic in zip(indexed, topics[1:]):
        args[param["name"]] = _static(param["type"], topic[2:])
    data = log.get("data") or "0x"
    parts = words(data)
    for position, param in enumerate(plain):
        if param["type"] in ("bytes", "string"):
            offset = int(parts[position], 16) // 32
            length = int(parts[offset], 16)
            blob = bytes.fromhex("".join(parts[offset + 1:])[: length * 2])
            args[param["name"]] = "0x" + blob.hex()
        else:
            args[param["name"]] = _static(param["type"], parts[position])
    return {"name": info["name"], "args": args}


def decode_evm_advance(blob: bytes) -> dict | None:
    """Split an EvmAdvance-encoded input into its fields; None if it is not one."""
    if len(blob) < 4 + 32 * 8 or "0x" + blob[:4].hex() != EVM_ADVANCE:
        return None
    body = blob[4:]
    word = lambda i: body[i * 32:(i + 1) * 32]  # noqa: E731
    offset = int.from_bytes(word(7), "big")
    length = int.from_bytes(body[offset: offset + 32], "big")
    return {
        "chain_id": int.from_bytes(word(0), "big"),
        "app": "0x" + word(1)[-20:].hex(),
        "sender": "0x" + word(2)[-20:].hex(),
        "block_number": int.from_bytes(word(3), "big"),
        "block_timestamp": int.from_bytes(word(4), "big"),
        "index": int.from_bytes(word(6), "big"),
        "payload": body[offset + 32: offset + 32 + length],
    }


def decode_revert(data: str | None) -> str | None:
    """Name a custom error or Error(string) from revert data."""
    if not data or len(data) < 10:
        return None
    head = data[:10].lower()
    if head == "0x08c379a0":
        try:
            parts = words(data[10:])
            length = int(parts[1], 16)
            return bytes.fromhex("".join(parts[2:])[: length * 2]).decode("utf-8", "replace")
        except (ValueError, IndexError):
            return "Error(string)"
    info = ERRORS.get(head)
    return info["name"] if info else head


def function_name(calldata: str | None) -> str | None:
    if not calldata or len(calldata) < 10:
        return None
    info = FUNCTIONS.get(calldata[:10].lower())
    return info["name"] if info else calldata[:10]
