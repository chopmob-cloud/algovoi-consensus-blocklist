"""
Node runner client for WalletBlocklistConsensus.

Used by the four AlgoVoi node runners to submit block/unblock votes.
Each runner holds a private key whose address was registered by the
contract owner. Votes are idempotent — submitting the same vote twice
is safe (the bitmask OR is a no-op).

Usage:
    runner = NodeRunnerClient(
        app_id=int(os.environ["BLOCKLIST_APP_ID"]),
        node_private_key=os.environ["NODE_RUNNER_KEY"],
    )
    result = runner.vote_block("WALLETADDRESSHERE...")
    if result["threshold_reached"]:
        print("Wallet is now blocked")
"""

from __future__ import annotations

import logging
from typing import Optional

import algosdk
from algosdk.abi import Method
from algosdk.atomic_transaction_composer import (
    AccountTransactionSigner,
    AtomicTransactionComposer,
)
from algosdk.v2client import algod as algod_module

logger = logging.getLogger(__name__)

_DEFAULT_ALGOD_ALGORAND = "https://mainnet-api.algonode.cloud"
_DEFAULT_ALGOD_VOI = "https://mainnet-api.voi.nodely.dev"
_WAIT_ROUNDS = 5


def default_algod_url(network: str = "algorand") -> str:
    """Return the default public algod URL for the given network."""
    return _DEFAULT_ALGOD_VOI if network == "voi" else _DEFAULT_ALGOD_ALGORAND

# ARC-4 method signatures — must match contract.py exactly
_M_VOTE_BLOCK = Method.from_signature("vote_block(address)bool")
_M_VOTE_UNBLOCK = Method.from_signature("vote_unblock(address)bool")
_M_REGISTER_NODE = Method.from_signature("register_node(address)uint64")
_M_REMOVE_NODE = Method.from_signature("remove_node(address)void")
_M_IS_BLOCKED = Method.from_signature("is_blocked(address)bool")
_M_GET_VOTE_COUNTS = Method.from_signature("get_vote_counts(address)(uint64,uint64)")
_M_DEPLOY = Method.from_signature("deploy(uint64)void")


def _box_ref(app_id: int, prefix: bytes, address: str) -> tuple[int, bytes]:
    """Build an algosdk box reference: (app_id, prefix + decoded_address)."""
    return (app_id, prefix + algosdk.encoding.decode_address(address))


class NodeRunnerClient:
    """
    Signs and submits vote_block / vote_unblock calls on behalf of one
    node runner. Requires the node's private key and the deployed app ID.

    Set network="voi" (and supply the VOI app_id) to vote on VOI mainnet.
    The contract logic is identical — only the algod endpoint differs.
    """

    def __init__(
        self,
        app_id: int,
        node_private_key: str,
        network: str = "algorand",
        algod_url: Optional[str] = None,
        algod_token: str = "",
    ) -> None:
        self.app_id = app_id
        self.network = network
        resolved_url = algod_url or default_algod_url(network)
        self.algod = algod_module.AlgodClient(algod_token, resolved_url)
        self.private_key = node_private_key
        self.address = algosdk.account.address_from_private_key(node_private_key)
        self.signer = AccountTransactionSigner(node_private_key)
        logger.info(
            "node_runner_client.init network=%s app_id=%d address=%s",
            network, app_id, self.address[:8],
        )

    # ------------------------------------------------------------------
    # Voting
    # ------------------------------------------------------------------

    def vote_block(self, wallet_address: str) -> dict:
        """
        Cast a block vote for `wallet_address`.

        Returns:
            {"tx_id": str, "threshold_reached": bool}

        Raises:
            algosdk.error.AlgodHTTPError — transaction rejected by the node
            AssertionError — wallet already blocked or caller not registered
        """
        self._validate_address(wallet_address)
        boxes = self._vote_boxes(wallet_address)
        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=self.app_id,
            method=_M_VOTE_BLOCK,
            sender=self.address,
            sp=self.algod.suggested_params(),
            signer=self.signer,
            method_args=[wallet_address],
            boxes=boxes,
        )
        result = atc.execute(self.algod, wait_rounds=_WAIT_ROUNDS)
        threshold_reached = bool(result.abi_results[0].return_value)
        logger.info(
            "vote_block network=%s wallet=%s tx=%s threshold_reached=%s",
            self.network, wallet_address[:8],
            result.tx_ids[0], threshold_reached,
        )
        return {"tx_id": result.tx_ids[0], "threshold_reached": threshold_reached, "network": self.network}

    def vote_unblock(self, wallet_address: str) -> dict:
        """
        Cast an unblock vote for a currently blocked `wallet_address`.

        Returns:
            {"tx_id": str, "threshold_reached": bool}
        """
        self._validate_address(wallet_address)
        boxes = self._vote_boxes(wallet_address)
        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=self.app_id,
            method=_M_VOTE_UNBLOCK,
            sender=self.address,
            sp=self.algod.suggested_params(),
            signer=self.signer,
            method_args=[wallet_address],
            boxes=boxes,
        )
        result = atc.execute(self.algod, wait_rounds=_WAIT_ROUNDS)
        threshold_reached = bool(result.abi_results[0].return_value)
        logger.info(
            "vote_unblock network=%s wallet=%s tx=%s threshold_reached=%s",
            self.network, wallet_address[:8],
            result.tx_ids[0], threshold_reached,
        )
        return {"tx_id": result.tx_ids[0], "threshold_reached": threshold_reached, "network": self.network}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _vote_boxes(self, wallet_address: str) -> list[tuple[int, bytes]]:
        """Box references needed for a vote call (node slot + all wallet boxes)."""
        aid = self.app_id
        return [
            _box_ref(aid, b"ns", self.address),      # caller's node slot
            _box_ref(aid, b"bv", wallet_address),    # block-vote bitmask
            _box_ref(aid, b"uv", wallet_address),    # unblock-vote bitmask
            _box_ref(aid, b"bl", wallet_address),    # blocked flag
        ]

    @staticmethod
    def _validate_address(addr: str) -> None:
        if not algosdk.encoding.is_valid_address(addr):
            raise ValueError(f"Invalid Algorand address: {addr!r}")


class OwnerClient:
    """
    Manages node registration and contract administration.
    Used by the platform operator (contract deployer) only.
    Set network="voi" to manage the VOI deployment.
    """

    def __init__(
        self,
        app_id: int,
        owner_private_key: str,
        network: str = "algorand",
        algod_url: Optional[str] = None,
        algod_token: str = "",
    ) -> None:
        self.app_id = app_id
        self.network = network
        resolved_url = algod_url or default_algod_url(network)
        self.algod = algod_module.AlgodClient(algod_token, resolved_url)
        self.private_key = owner_private_key
        self.address = algosdk.account.address_from_private_key(owner_private_key)
        self.signer = AccountTransactionSigner(owner_private_key)

    def register_node(self, node_address: str) -> dict:
        """Register a node runner address. Returns {"tx_id", "slot"}."""
        NodeRunnerClient._validate_address(node_address)
        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=self.app_id,
            method=_M_REGISTER_NODE,
            sender=self.address,
            sp=self.algod.suggested_params(),
            signer=self.signer,
            method_args=[node_address],
            boxes=[_box_ref(self.app_id, b"ns", node_address)],
        )
        result = atc.execute(self.algod, wait_rounds=_WAIT_ROUNDS)
        slot = int(result.abi_results[0].return_value)
        logger.info("register_node node=%s slot=%d tx=%s", node_address[:8], slot, result.tx_ids[0])
        return {"tx_id": result.tx_ids[0], "slot": slot}

    def remove_node(self, node_address: str) -> str:
        """Remove a node runner. Returns tx_id."""
        NodeRunnerClient._validate_address(node_address)
        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=self.app_id,
            method=_M_REMOVE_NODE,
            sender=self.address,
            sp=self.algod.suggested_params(),
            signer=self.signer,
            method_args=[node_address],
            boxes=[_box_ref(self.app_id, b"ns", node_address)],
        )
        result = atc.execute(self.algod, wait_rounds=_WAIT_ROUNDS)
        return result.tx_ids[0]
