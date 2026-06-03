/**
 * BlocklistSim — in-memory simulation of WalletBlocklistConsensus.
 *
 * Faithfully reproduces the AVM contract logic in plain JavaScript so
 * scenarios can be run without a live node, algosdk, or compilation.
 *
 * The bitmask arithmetic, threshold checks, and state transitions are
 * identical to contract.py so this can be used for logic verification
 * before deploying to testnet.
 */

export class BlocklistSimError extends Error {
  constructor(message) {
    super(message);
    this.name = "BlocklistSimError";
  }
}

export class BlocklistSim {
  /**
   * @param {number} threshold  Number of node votes required (default 4).
   * @param {string} owner      Address string of the contract owner.
   */
  constructor(threshold = 4, owner = "OWNER") {
    if (threshold < 1 || threshold > 64) {
      throw new BlocklistSimError("threshold must be 1–64");
    }
    this.threshold   = threshold;
    this.owner       = owner;
    this.nodeSlots   = new Map();   // address -> slot (1-based BigInt)
    this.blockVotes  = new Map();   // wallet  -> BigInt bitmask
    this.unblockVotes = new Map();  // wallet  -> BigInt bitmask
    this.blockedSet  = new Set();   // wallet addresses currently blocked
    this.nodeCount   = 0n;
  }

  // -------------------------------------------------------------------------
  // Node management
  // -------------------------------------------------------------------------

  /**
   * Register a node runner. Returns the assigned slot (1-based).
   * @param {string} caller
   * @param {string} nodeAddress
   * @returns {number}
   */
  registerNode(caller, nodeAddress) {
    this._assertOwner(caller);
    if (this.nodeCount >= 64n) throw new BlocklistSimError("max 64 nodes");
    if (this.nodeSlots.has(nodeAddress)) throw new BlocklistSimError("node already registered");
    const slot = ++this.nodeCount;
    this.nodeSlots.set(nodeAddress, slot);
    return Number(slot);
  }

  /**
   * Deregister a node runner. Slot is permanently retired.
   * @param {string} caller
   * @param {string} nodeAddress
   */
  removeNode(caller, nodeAddress) {
    this._assertOwner(caller);
    if (!this.nodeSlots.has(nodeAddress)) throw new BlocklistSimError("node not registered");
    this.nodeSlots.delete(nodeAddress);
  }

  updateThreshold(caller, newThreshold) {
    this._assertOwner(caller);
    if (newThreshold < 1 || newThreshold > 64) {
      throw new BlocklistSimError("threshold must be 1–64");
    }
    this.threshold = newThreshold;
  }

  // -------------------------------------------------------------------------
  // Voting
  // -------------------------------------------------------------------------

  /**
   * Cast a block vote for `walletAddress`.
   * @param {string} nodeAddress  Must be a registered node runner.
   * @param {string} walletAddress
   * @returns {{ thresholdReached: boolean, blockVotes: number }}
   */
  voteBlock(nodeAddress, walletAddress) {
    const slot = this._assertNode(nodeAddress);
    if (this.blockedSet.has(walletAddress)) {
      throw new BlocklistSimError("wallet is already blocked");
    }

    const bit = 1n << (slot - 1n);
    const current = this.blockVotes.get(walletAddress) ?? 0n;
    const updated = current | bit;
    this.blockVotes.set(walletAddress, updated);

    const count = popcount(updated);
    if (count >= this.threshold) {
      this.blockedSet.add(walletAddress);
      this.unblockVotes.set(walletAddress, 0n);
      return { thresholdReached: true, blockVotes: count };
    }
    return { thresholdReached: false, blockVotes: count };
  }

  /**
   * Cast an unblock vote for a currently blocked `walletAddress`.
   * @param {string} nodeAddress
   * @param {string} walletAddress
   * @returns {{ thresholdReached: boolean, unblockVotes: number }}
   */
  voteUnblock(nodeAddress, walletAddress) {
    const slot = this._assertNode(nodeAddress);
    if (!this.blockedSet.has(walletAddress)) {
      throw new BlocklistSimError("wallet is not blocked");
    }

    const bit = 1n << (slot - 1n);
    const current = this.unblockVotes.get(walletAddress) ?? 0n;
    const updated = current | bit;
    this.unblockVotes.set(walletAddress, updated);

    const count = popcount(updated);
    if (count >= this.threshold) {
      this.blockedSet.delete(walletAddress);
      this.blockVotes.delete(walletAddress);
      this.unblockVotes.delete(walletAddress);
      return { thresholdReached: true, unblockVotes: count };
    }
    return { thresholdReached: false, unblockVotes: count };
  }

  // -------------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------------

  isBlocked(walletAddress) {
    return this.blockedSet.has(walletAddress);
  }

  getVoteCounts(walletAddress) {
    return {
      blockVotes:   popcount(this.blockVotes.get(walletAddress)   ?? 0n),
      unblockVotes: popcount(this.unblockVotes.get(walletAddress) ?? 0n),
    };
  }

  getNodeSlot(nodeAddress) {
    return Number(this.nodeSlots.get(nodeAddress) ?? 0n);
  }

  // -------------------------------------------------------------------------
  // State snapshot (for debugging)
  // -------------------------------------------------------------------------

  snapshot() {
    return {
      threshold:    this.threshold,
      nodeCount:    Number(this.nodeCount),
      nodes:        Object.fromEntries(
        [...this.nodeSlots.entries()].map(([k, v]) => [k, Number(v)])
      ),
      blocked:      [...this.blockedSet],
      pendingBlock: Object.fromEntries(
        [...this.blockVotes.entries()]
          .filter(([w]) => !this.blockedSet.has(w))
          .map(([w, mask]) => [w, { votes: popcount(mask), mask: `0b${mask.toString(2)}` }])
      ),
    };
  }

  // -------------------------------------------------------------------------
  // Private
  // -------------------------------------------------------------------------

  _assertOwner(caller) {
    if (caller !== this.owner) throw new BlocklistSimError("owner only");
  }

  _assertNode(address) {
    const slot = this.nodeSlots.get(address);
    if (slot === undefined) throw new BlocklistSimError("caller is not a registered node");
    return slot;
  }
}

/** Count set bits in a BigInt (popcount). */
export function popcount(n) {
  let count = 0;
  while (n > 0n) { count += Number(n & 1n); n >>= 1n; }
  return count;
}
