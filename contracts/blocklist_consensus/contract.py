"""
WalletBlocklistConsensus — Algorand AVM smart contract (algopy ARC-4).

4-of-N on-chain consensus blocklist. Up to 64 registered node runners each
hold one vote slot (a bit position in a UInt64 bitmask). When the number of
distinct "block" votes for a wallet address reaches `threshold` (default 4),
the wallet is marked blocked. Unblocking requires the same threshold of
"unblock" votes, allowing false-positive remediation.

Box layout  (key = prefix + 32-byte ARC-4 address):
  ns:<addr32>  UInt64  — node slot index (1-based); absent = not registered
  bv:<addr32>  UInt64  — block-vote bitmask (bit N-1 = node in slot N voted)
  uv:<addr32>  UInt64  — unblock-vote bitmask
  bl:<addr32>  UInt64  — blocked flag; nonzero = blocked

Opcode cost notes:
  - popcount is O(1) (AVM opcode)
  - Bit-shift and OR are O(1)
  - Four box reads per vote call → 4 × 1000 opcode budget used

Deploy once per chain. Set threshold=4 to require all four AlgoVoi node
runners to agree before a wallet is blocked.
"""

from algopy import (
    Account,
    ARC4Contract,
    BoxMap,
    GlobalState,
    Txn,
    UInt64,
    op,
)
from algopy import arc4


class VoteCounts(arc4.Struct):
    """ABI-returnable vote summary for a wallet."""
    block_votes: arc4.UInt64
    unblock_votes: arc4.UInt64


class WalletBlocklistConsensus(ARC4Contract):
    """Consensus-gated on-chain wallet blocklist for the AlgoVoi gateway."""

    def __init__(self) -> None:
        self.owner = GlobalState(Account, key=b"owner")
        self.threshold = GlobalState(UInt64, key=b"t")
        self.node_count = GlobalState(UInt64, key=b"nc")

        self.node_slots: BoxMap[arc4.Address, UInt64] = BoxMap(
            arc4.Address, UInt64, key_prefix=b"ns"
        )
        self.block_votes: BoxMap[arc4.Address, UInt64] = BoxMap(
            arc4.Address, UInt64, key_prefix=b"bv"
        )
        self.unblock_votes: BoxMap[arc4.Address, UInt64] = BoxMap(
            arc4.Address, UInt64, key_prefix=b"uv"
        )
        self.blocked_map: BoxMap[arc4.Address, UInt64] = BoxMap(
            arc4.Address, UInt64, key_prefix=b"bl"
        )

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    @arc4.abimethod(create="require")
    def deploy(self, threshold: UInt64) -> None:
        """Deploy the contract. threshold is typically 4."""
        assert threshold >= UInt64(1), "threshold >= 1"
        assert threshold <= UInt64(64), "threshold <= 64"
        self.owner.value = Txn.sender
        self.threshold.value = threshold
        self.node_count.value = UInt64(0)

    # -------------------------------------------------------------------------
    # Node management (owner only)
    # -------------------------------------------------------------------------

    @arc4.abimethod
    def register_node(self, node: arc4.Address) -> UInt64:
        """
        Register a node runner address. Assigns the next available slot (1-64).
        Returns the assigned slot number. Owner only.
        """
        assert Txn.sender == self.owner.value, "owner only"
        count = self.node_count.value
        assert count < UInt64(64), "max 64 nodes"
        _, already = self.node_slots.maybe(node)
        assert not already, "node already registered"
        slot = count + UInt64(1)
        self.node_slots[node] = slot
        self.node_count.value = slot
        return slot

    @arc4.abimethod
    def remove_node(self, node: arc4.Address) -> None:
        """
        Deregister a node runner. Their slot bit is permanently retired —
        the slot index is not reused to prevent replay of old signatures.
        Owner only.
        """
        assert Txn.sender == self.owner.value, "owner only"
        _, exists = self.node_slots.maybe(node)
        assert exists, "node not registered"
        del self.node_slots[node]

    @arc4.abimethod
    def update_threshold(self, new_threshold: UInt64) -> None:
        """Adjust the vote threshold. Owner only."""
        assert Txn.sender == self.owner.value, "owner only"
        assert new_threshold >= UInt64(1), "threshold >= 1"
        assert new_threshold <= UInt64(64), "threshold <= 64"
        self.threshold.value = new_threshold

    @arc4.abimethod
    def transfer_ownership(self, new_owner: arc4.Address) -> None:
        """Transfer contract ownership. Owner only."""
        assert Txn.sender == self.owner.value, "owner only"
        self.owner.value = Account(new_owner.bytes)

    # -------------------------------------------------------------------------
    # Voting
    # -------------------------------------------------------------------------

    @arc4.abimethod
    def vote_block(self, wallet: arc4.Address) -> arc4.Bool:
        """
        Cast a block vote for `wallet`. Caller must be a registered node runner.
        Idempotent: re-voting by the same node is a no-op on the bitmask.
        Returns True if this vote causes the block threshold to be reached.
        """
        sender = arc4.Address(Txn.sender)
        slot, is_node = self.node_slots.maybe(sender)
        assert is_node, "caller is not a registered node"

        _, already_blocked = self.blocked_map.maybe(wallet)
        assert not already_blocked, "wallet is already blocked"

        # Set the caller's bit in the block-vote bitmask (idempotent OR)
        bit = UInt64(1) << (slot - UInt64(1))
        bv, _ = self.block_votes.maybe(wallet)
        new_bv = bv | bit
        self.block_votes[wallet] = new_bv

        if op.popcount(new_bv) >= self.threshold.value:
            self.blocked_map[wallet] = UInt64(1)
            # Clear any residual unblock votes
            self.unblock_votes[wallet] = UInt64(0)
            return arc4.Bool(True)
        return arc4.Bool(False)

    @arc4.abimethod
    def vote_unblock(self, wallet: arc4.Address) -> arc4.Bool:
        """
        Cast an unblock vote for a currently blocked `wallet`.
        Returns True if the unblock threshold is reached and the wallet
        is removed from the blocklist. All vote state is cleared on unblock.
        """
        sender = arc4.Address(Txn.sender)
        slot, is_node = self.node_slots.maybe(sender)
        assert is_node, "caller is not a registered node"

        blocked_val, is_blocked = self.blocked_map.maybe(wallet)
        assert is_blocked and blocked_val > UInt64(0), "wallet is not blocked"

        bit = UInt64(1) << (slot - UInt64(1))
        uv, _ = self.unblock_votes.maybe(wallet)
        new_uv = uv | bit
        self.unblock_votes[wallet] = new_uv

        if op.popcount(new_uv) >= self.threshold.value:
            # Fully clear all state for this wallet
            del self.blocked_map[wallet]
            del self.block_votes[wallet]
            del self.unblock_votes[wallet]
            return arc4.Bool(True)
        return arc4.Bool(False)

    # -------------------------------------------------------------------------
    # Readonly queries (simulate with no fee)
    # -------------------------------------------------------------------------

    @arc4.abimethod(readonly=True)
    def is_blocked(self, wallet: arc4.Address) -> arc4.Bool:
        """Returns True if the wallet is currently on the blocklist."""
        val, exists = self.blocked_map.maybe(wallet)
        if not exists:
            return arc4.Bool(False)
        return arc4.Bool(val > UInt64(0))

    @arc4.abimethod(readonly=True)
    def get_vote_counts(self, wallet: arc4.Address) -> VoteCounts:
        """Returns the current block and unblock vote counts for a wallet."""
        bv, _ = self.block_votes.maybe(wallet)
        uv, _ = self.unblock_votes.maybe(wallet)
        return VoteCounts(
            block_votes=arc4.UInt64(op.popcount(bv)),
            unblock_votes=arc4.UInt64(op.popcount(uv)),
        )

    @arc4.abimethod(readonly=True)
    def get_node_slot(self, node: arc4.Address) -> arc4.UInt64:
        """Returns the slot number of a node runner (0 if not registered)."""
        slot, exists = self.node_slots.maybe(node)
        if not exists:
            return arc4.UInt64(0)
        return arc4.UInt64(slot)
