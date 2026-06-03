"""
Unit tests for WalletBlocklistConsensus using algopy.testing.

Run:
    pytest contracts/blocklist_consensus/tests/test_blocklist.py -v

Requirements:
    pip install algorand-python[testing] pytest
"""

from __future__ import annotations

import pytest

from algopy import UInt64, arc4
from algopy.testing import AlgopyTestContext, arc4_value

from contracts.blocklist_consensus.contract import WalletBlocklistConsensus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    with AlgopyTestContext() as context:
        yield context


@pytest.fixture
def contract(ctx):
    return WalletBlocklistConsensus()


@pytest.fixture
def owner(ctx):
    return ctx.any_account()


@pytest.fixture
def nodes(ctx):
    return [ctx.any_account() for _ in range(4)]


@pytest.fixture
def wallet(ctx):
    return ctx.any_account()


def _addr(account) -> arc4.Address:
    return arc4.Address(account)


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------

def test_deploy_sets_threshold(ctx, contract, owner):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    assert contract.threshold.value == UInt64(4)
    assert contract.node_count.value == UInt64(0)
    assert contract.owner.value == owner


def test_deploy_rejects_zero_threshold(ctx, contract, owner):
    ctx.set_sender(owner)
    with pytest.raises(Exception, match="threshold >= 1"):
        contract.deploy(UInt64(0))


def test_deploy_rejects_threshold_above_64(ctx, contract, owner):
    ctx.set_sender(owner)
    with pytest.raises(Exception, match="threshold <= 64"):
        contract.deploy(UInt64(65))


# ---------------------------------------------------------------------------
# Node registration
# ---------------------------------------------------------------------------

def test_register_node_assigns_slots(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    for i, node in enumerate(nodes, start=1):
        ctx.set_sender(owner)
        slot = contract.register_node(_addr(node))
        assert slot == UInt64(i)
    assert contract.node_count.value == UInt64(4)


def test_register_node_non_owner_rejected(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    intruder = ctx.any_account()
    ctx.set_sender(intruder)
    with pytest.raises(Exception, match="owner only"):
        contract.register_node(_addr(nodes[0]))


def test_register_node_duplicate_rejected(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    ctx.set_sender(owner)
    contract.register_node(_addr(nodes[0]))
    ctx.set_sender(owner)
    with pytest.raises(Exception, match="already registered"):
        contract.register_node(_addr(nodes[0]))


def test_remove_node(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    ctx.set_sender(owner)
    contract.register_node(_addr(nodes[0]))
    ctx.set_sender(owner)
    contract.remove_node(_addr(nodes[0]))
    # Slot should no longer exist
    assert contract.get_node_slot(_addr(nodes[0])) == arc4.UInt64(0)


def test_remove_unregistered_node_rejected(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    ctx.set_sender(owner)
    with pytest.raises(Exception, match="node not registered"):
        contract.remove_node(_addr(nodes[0]))


# ---------------------------------------------------------------------------
# Voting — below threshold
# ---------------------------------------------------------------------------

def _setup(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    for node in nodes:
        ctx.set_sender(owner)
        contract.register_node(_addr(node))


def test_three_votes_does_not_block(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    for node in nodes[:3]:
        ctx.set_sender(node)
        result = contract.vote_block(_addr(wallet))
        assert result == arc4.Bool(False)

    assert contract.is_blocked(_addr(wallet)) == arc4.Bool(False)
    counts = contract.get_vote_counts(_addr(wallet))
    assert counts.block_votes == arc4.UInt64(3)
    assert counts.unblock_votes == arc4.UInt64(0)


# ---------------------------------------------------------------------------
# Voting — threshold reached (4-of-4)
# ---------------------------------------------------------------------------

def test_fourth_vote_triggers_block(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    for node in nodes[:3]:
        ctx.set_sender(node)
        contract.vote_block(_addr(wallet))

    ctx.set_sender(nodes[3])
    result = contract.vote_block(_addr(wallet))
    assert result == arc4.Bool(True)
    assert contract.is_blocked(_addr(wallet)) == arc4.Bool(True)


def test_already_blocked_vote_rejected(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    for node in nodes:
        ctx.set_sender(node)
        contract.vote_block(_addr(wallet))

    ctx.set_sender(nodes[0])
    with pytest.raises(Exception, match="already blocked"):
        contract.vote_block(_addr(wallet))


# ---------------------------------------------------------------------------
# Idempotency — same node votes twice
# ---------------------------------------------------------------------------

def test_double_vote_is_idempotent(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    ctx.set_sender(nodes[0])
    contract.vote_block(_addr(wallet))
    ctx.set_sender(nodes[0])
    result = contract.vote_block(_addr(wallet))
    # Still only 1 vote (the bit was already set)
    assert result == arc4.Bool(False)
    counts = contract.get_vote_counts(_addr(wallet))
    assert counts.block_votes == arc4.UInt64(1)


# ---------------------------------------------------------------------------
# Unblocking
# ---------------------------------------------------------------------------

def test_unblock_requires_threshold(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    for node in nodes:
        ctx.set_sender(node)
        contract.vote_block(_addr(wallet))

    # 3 unblock votes — not enough
    for node in nodes[:3]:
        ctx.set_sender(node)
        result = contract.vote_unblock(_addr(wallet))
        assert result == arc4.Bool(False)
    assert contract.is_blocked(_addr(wallet)) == arc4.Bool(True)


def test_fourth_unblock_clears_wallet(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    for node in nodes:
        ctx.set_sender(node)
        contract.vote_block(_addr(wallet))

    for node in nodes[:3]:
        ctx.set_sender(node)
        contract.vote_unblock(_addr(wallet))

    ctx.set_sender(nodes[3])
    result = contract.vote_unblock(_addr(wallet))
    assert result == arc4.Bool(True)
    assert contract.is_blocked(_addr(wallet)) == arc4.Bool(False)

    # Vote state should be cleared
    counts = contract.get_vote_counts(_addr(wallet))
    assert counts.block_votes == arc4.UInt64(0)
    assert counts.unblock_votes == arc4.UInt64(0)


def test_unblock_unblocked_wallet_rejected(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    ctx.set_sender(nodes[0])
    with pytest.raises(Exception, match="wallet is not blocked"):
        contract.vote_unblock(_addr(wallet))


# ---------------------------------------------------------------------------
# Unregistered caller
# ---------------------------------------------------------------------------

def test_unregistered_caller_cannot_vote(ctx, contract, owner, nodes, wallet):
    _setup(ctx, contract, owner, nodes)
    outsider = ctx.any_account()
    ctx.set_sender(outsider)
    with pytest.raises(Exception, match="caller is not a registered node"):
        contract.vote_block(_addr(wallet))


# ---------------------------------------------------------------------------
# Threshold update
# ---------------------------------------------------------------------------

def test_update_threshold(ctx, contract, owner):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    ctx.set_sender(owner)
    contract.update_threshold(UInt64(3))
    assert contract.threshold.value == UInt64(3)


def test_update_threshold_non_owner_rejected(ctx, contract, owner):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    outsider = ctx.any_account()
    ctx.set_sender(outsider)
    with pytest.raises(Exception, match="owner only"):
        contract.update_threshold(UInt64(3))


# ---------------------------------------------------------------------------
# Ownership transfer
# ---------------------------------------------------------------------------

def test_transfer_ownership(ctx, contract, owner, nodes):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    new_owner = ctx.any_account()
    ctx.set_sender(owner)
    contract.transfer_ownership(_addr(new_owner))
    assert contract.owner.value == new_owner
    # Old owner can no longer manage nodes
    ctx.set_sender(owner)
    with pytest.raises(Exception, match="owner only"):
        contract.register_node(_addr(nodes[0]))


# ---------------------------------------------------------------------------
# is_blocked on unknown wallet
# ---------------------------------------------------------------------------

def test_is_blocked_returns_false_for_unknown_wallet(ctx, contract, owner):
    ctx.set_sender(owner)
    contract.deploy(UInt64(4))
    stranger = ctx.any_account()
    assert contract.is_blocked(_addr(stranger)) == arc4.Bool(False)
