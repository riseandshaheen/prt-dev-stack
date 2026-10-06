#!/usr/bin/env python3
"""Regenerate vis/abi.json from the rollups-node Go contract bindings.

    python3 tools/gen_abi.py /path/to/rollups-node

Run it whenever the stack moves to new contracts. The server checks at startup
that the event topics it depends on are present in the file.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vis.keccak import keccak_hex  # noqa: E402

PACKAGES = {
    "itournament": "Tournament",
    "idaveconsensus": "DaveConsensus",
    "iinputbox": "InputBox",
    "iapplication": "Application",
    "idaveappfactory": "DaveAppFactory",
    "imultileveltournamentfactory": "TournamentFactory",
}


def canonical(param: dict) -> str:
    kind = param["type"]
    if kind.startswith("tuple"):
        inner = ",".join(canonical(c) for c in param["components"])
        return f"({inner}){kind[5:]}"
    return kind


def load_abi(go_file: Path) -> list[dict]:
    text = go_file.read_text()
    match = re.search(r'ABI: "(.*?)",\n', text)
    if not match:
        raise SystemExit(f"no ABI in {go_file}")
    return json.loads(json.loads(f'"{match.group(1)}"'))


def main() -> None:
    root = Path(sys.argv[1]) / "pkg" / "contracts"
    out = {"events": {}, "functions": {}, "errors": {}}
    for package, contract in PACKAGES.items():
        for item in load_abi(root / package / f"{package}.go"):
            if item["type"] not in ("event", "function", "error"):
                continue
            signature = f'{item["name"]}({",".join(canonical(p) for p in item["inputs"])})'
            digest = keccak_hex(signature)
            if item["type"] == "event":
                out["events"][digest] = {
                    "name": item["name"],
                    "contract": contract,
                    "signature": signature,
                    "inputs": [
                        {"name": p["name"], "type": canonical(p), "indexed": p.get("indexed", False)}
                        for p in item["inputs"]
                    ],
                }
            elif item["type"] == "function":
                out["functions"][digest[:10]] = {"name": item["name"], "contract": contract}
            else:
                out["errors"][digest[:10]] = {
                    "name": item["name"],
                    "signature": signature,
                    "inputs": [{"name": p["name"], "type": canonical(p)} for p in item["inputs"]],
                }
    target = Path(__file__).resolve().parent.parent / "vis" / "abi.json"
    target.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{len(out['events'])} events, {len(out['functions'])} functions, {len(out['errors'])} errors -> {target}")


if __name__ == "__main__":
    main()
