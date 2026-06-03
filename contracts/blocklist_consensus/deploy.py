"""
Deploy WalletBlocklistConsensus to Algorand mainnet (or testnet).

Prerequisites:
    pip install algokit-utils algosdk
    algokit compile contracts/blocklist_consensus/contract.py
        → writes contracts/blocklist_consensus/artifacts/
              WalletBlocklistConsensus.approval.teal
              WalletBlocklistConsensus.clear.teal
              WalletBlocklistConsensus.arc56.json

Usage:
    # Deploy fresh and register all four node runners:
    python -m contracts.blocklist_consensus.deploy \\
        --threshold 4 \\
        --owner-mnemonic "$OWNER_MNEMONIC" \\
        --nodes "ADDR1,ADDR2,ADDR3,ADDR4"

    # Register a node on an already-deployed contract:
    python -m contracts.blocklist_consensus.deploy \\
        --app-id 1234567 \\
        --owner-mnemonic "$OWNER_MNEMONIC" \\
        --register-node "ADDR5"

Environment:
    ALGOD_URL   — defaults to https://mainnet-api.algonode.cloud
    ALGOD_TOKEN — defaults to empty (public node)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import algosdk
from algosdk import transaction as atxn
from algosdk.abi import Method
from algosdk.v2client import algod as algod_module

from contracts.blocklist_consensus.client import OwnerClient, _DEFAULT_ALGOD_ALGORAND, _DEFAULT_ALGOD_VOI

_ARTIFACTS = Path(__file__).parent / "artifacts"
_APPROVAL_TEAL = _ARTIFACTS / "WalletBlocklistConsensus.approval.teal"
_CLEAR_TEAL = _ARTIFACTS / "WalletBlocklistConsensus.clear.teal"

_M_DEPLOY = Method.from_signature("deploy(uint64)void")


def _algod(url: str, token: str) -> algod_module.AlgodClient:
    return algod_module.AlgodClient(token, url)


def _compile_teal(client: algod_module.AlgodClient, teal_path: Path) -> bytes:
    source = teal_path.read_text()
    resp = client.compile(source)
    import base64
    return base64.b64decode(resp["result"])


def deploy_contract(
    algod: algod_module.AlgodClient,
    owner_private_key: str,
    threshold: int,
) -> int:
    """
    Compile + deploy the contract.
    Returns the deployed application ID.
    """
    if not _APPROVAL_TEAL.exists():
        sys.exit(
            f"Compiled TEAL not found at {_APPROVAL_TEAL}. "
            "Run: algokit compile contracts/blocklist_consensus/contract.py"
        )

    approval = _compile_teal(algod, _APPROVAL_TEAL)
    clear = _compile_teal(algod, _CLEAR_TEAL)

    owner_address = algosdk.account.address_from_private_key(owner_private_key)
    signer = AccountTransactionSigner(owner_private_key)
    sp = algod.suggested_params()

    # Box budget: each box read/write costs 2500 + 400 × bytes.
    # We don't pre-fund boxes here; Algorand charges box MBR from the app account.
    # The owner should fund the app account with enough ALGO for MBR before
    # registering nodes / receiving votes.
    #
    # Per-box MBR = 2500 + 400 × (key_len + value_len)
    # node slot box: 2 + 32 + 8 = 42 bytes → 2500 + 400×42 = 19300 µAlgo
    # vote/blocked boxes: 2 + 32 + 8 = 42 bytes → same
    # Budget for 4 nodes + 1000 blocklist entries: ~20 000 × 1005 µAlgo = 0.2 ALGO
    # Recommend funding the app with ≥ 1 ALGO initially.

    # ARC-4 ABI encoding: 4-byte selector + ABI-encoded argument(s)
    selector = _M_DEPLOY.get_selector()
    # threshold is a single uint64 — ABI encodes as 8 big-endian bytes
    threshold_encoded = threshold.to_bytes(8, "big")

    create_txn_final = atxn.ApplicationCreateTxn(
        sender=owner_address,
        sp=sp,
        on_complete=atxn.OnComplete.NoOpOC,
        approval_program=approval,
        clear_program=clear,
        global_schema=atxn.StateSchema(num_uints=3, num_byte_slices=1),
        local_schema=atxn.StateSchema(num_uints=0, num_byte_slices=0),
        app_args=[selector + threshold_encoded],
    )

    signed = create_txn_final.sign(owner_private_key)
    tx_id = algod.send_transaction(signed)
    print(f"Deploy tx submitted: {tx_id}")

    result = atxn.wait_for_confirmation(algod, tx_id, wait_rounds=10)
    app_id = result["application-index"]
    print(f"Deployed WalletBlocklistConsensus: app_id={app_id}")
    return app_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy WalletBlocklistConsensus")
    parser.add_argument("--app-id", type=int, help="Existing app ID (skip deploy)")
    parser.add_argument("--threshold", type=int, default=4, help="Vote threshold (default 4)")
    parser.add_argument("--owner-mnemonic", required=True, help="Owner account mnemonic")
    parser.add_argument("--nodes", help="Comma-separated node runner addresses to register")
    parser.add_argument("--register-node", help="Register a single additional node")
    parser.add_argument(
        "--network",
        choices=["algorand", "voi", "algorand-testnet", "voi-testnet"],
        default="algorand",
        help="Target network (default: algorand mainnet)",
    )
    args = parser.parse_args()

    _NETWORK_ALGOD_DEFAULTS = {
        "algorand":         _DEFAULT_ALGOD_ALGORAND,
        "voi":              _DEFAULT_ALGOD_VOI,
        "algorand-testnet": "https://testnet-api.algonode.cloud",
        "voi-testnet":      "https://testnet-api.voi.nodely.dev",
    }
    algod_url = os.environ.get("ALGOD_URL", _NETWORK_ALGOD_DEFAULTS[args.network])
    algod_token = os.environ.get("ALGOD_TOKEN", "")

    owner_private_key = algosdk.mnemonic.to_private_key(args.owner_mnemonic)
    owner_address = algosdk.account.address_from_private_key(owner_private_key)
    print(f"Owner: {owner_address}")

    client = algod_module.AlgodClient(algod_token, algod_url)

    app_id = args.app_id
    if app_id is None:
        app_id = deploy_contract(client, owner_private_key, args.threshold)
        print(f"\nSet BLOCKLIST_APP_ID={app_id} in your environment.\n")

    owner_client = OwnerClient(
        app_id=app_id,
        owner_private_key=owner_private_key,
        network=args.network.split("-")[0],  # "voi-testnet" -> "voi"
        algod_url=algod_url,
        algod_token=algod_token,
    )

    # Register initial nodes
    if args.nodes:
        addresses = [a.strip() for a in args.nodes.split(",") if a.strip()]
        for addr in addresses:
            result = owner_client.register_node(addr)
            print(f"  Registered node {addr[:8]}... slot={result['slot']} tx={result['tx_id']}")

    # Register a single additional node
    if args.register_node:
        result = owner_client.register_node(args.register_node)
        print(f"Registered node {args.register_node[:8]}... slot={result['slot']} tx={result['tx_id']}")

    print("\nDone.")
    summary = {"app_id": app_id, "threshold": args.threshold, "network": args.network}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
