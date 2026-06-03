/**
 * Simulation scenarios for WalletBlocklistConsensus.
 *
 * Each scenario is a self-contained function that receives a fresh
 * BlocklistSim and returns { passed, steps }.
 *
 * Run via:  node simulation/run.js
 */

import { BlocklistSim, BlocklistSimError } from "./BlocklistSim.js";

// ---------------------------------------------------------------------------
// Named actors used across scenarios
// ---------------------------------------------------------------------------

const OWNER  = "OWNER_ADDR";
const NODES  = ["NODE_1", "NODE_2", "NODE_3", "NODE_4", "NODE_5"];
const WALLET = "WALLET_TARGET";
const WALLET2 = "WALLET_TARGET_2";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fresh(threshold = 4) {
  const sim = new BlocklistSim(threshold, OWNER);
  for (const node of NODES.slice(0, 4)) sim.registerNode(OWNER, node);
  return sim;
}

function assertThrows(fn, expectedMsg) {
  try {
    fn();
    return { ok: false, detail: `Expected error "${expectedMsg}" but no error was thrown` };
  } catch (e) {
    if (e instanceof BlocklistSimError && e.message.includes(expectedMsg)) {
      return { ok: true };
    }
    return { ok: false, detail: `Wrong error — got "${e.message}", expected "${expectedMsg}"` };
  }
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

const SCENARIOS = [

  // --- Basic block flow ----------------------------------------------------

  {
    name: "4-of-4 block reaches threshold",
    run() {
      const sim = fresh();
      const steps = [];

      for (let i = 0; i < 3; i++) {
        const r = sim.voteBlock(NODES[i], WALLET);
        steps.push(`Node ${i + 1} voted — blockVotes=${r.blockVotes}, thresholdReached=${r.thresholdReached}`);
        if (r.thresholdReached) return { passed: false, steps, detail: "Threshold should not be reached at vote " + (i + 1) };
      }

      const r4 = sim.voteBlock(NODES[3], WALLET);
      steps.push(`Node 4 voted — blockVotes=${r4.blockVotes}, thresholdReached=${r4.thresholdReached}`);

      if (!r4.thresholdReached) return { passed: false, steps, detail: "Threshold should be reached on 4th vote" };
      if (!sim.isBlocked(WALLET)) return { passed: false, steps, detail: "Wallet should be blocked" };

      steps.push(`isBlocked(WALLET) = ${sim.isBlocked(WALLET)}`);
      return { passed: true, steps };
    },
  },

  // --- Threshold not reached -----------------------------------------------

  {
    name: "3-of-4 votes does not block wallet",
    run() {
      const sim = fresh();
      const steps = [];

      for (let i = 0; i < 3; i++) {
        const r = sim.voteBlock(NODES[i], WALLET);
        steps.push(`Node ${i + 1} voted — blockVotes=${r.blockVotes}`);
      }

      const blocked = sim.isBlocked(WALLET);
      steps.push(`isBlocked after 3 votes = ${blocked}`);
      const counts = sim.getVoteCounts(WALLET);
      steps.push(`getVoteCounts = ${JSON.stringify(counts)}`);

      if (blocked) return { passed: false, steps, detail: "Wallet should NOT be blocked with 3 votes" };
      if (counts.blockVotes !== 3) return { passed: false, steps, detail: "Expected 3 block votes" };

      return { passed: true, steps };
    },
  },

  // --- Idempotency ----------------------------------------------------------

  {
    name: "Same node voting twice counts as one vote",
    run() {
      const sim = fresh();
      const steps = [];

      sim.voteBlock(NODES[0], WALLET);
      const r2 = sim.voteBlock(NODES[0], WALLET); // re-vote — bitmask OR is no-op
      steps.push(`Node 1 voted twice — blockVotes=${r2.blockVotes}`);

      if (r2.blockVotes !== 1) {
        return { passed: false, steps, detail: `Expected 1 vote, got ${r2.blockVotes}` };
      }

      // Now have nodes 2, 3, 4 vote — should reach threshold (total = 4 distinct nodes)
      sim.voteBlock(NODES[1], WALLET);
      sim.voteBlock(NODES[2], WALLET);
      const r = sim.voteBlock(NODES[3], WALLET);
      steps.push(`After distinct 4th vote — thresholdReached=${r.thresholdReached}`);

      if (!r.thresholdReached) return { passed: false, steps, detail: "Should reach threshold with 4 distinct nodes" };
      return { passed: true, steps };
    },
  },

  // --- Unblock flow ---------------------------------------------------------

  {
    name: "4-of-4 unblock removes wallet from blocklist",
    run() {
      const sim = fresh();
      const steps = [];

      for (const node of NODES.slice(0, 4)) sim.voteBlock(node, WALLET);
      steps.push(`Wallet blocked: ${sim.isBlocked(WALLET)}`);

      for (let i = 0; i < 3; i++) {
        const r = sim.voteUnblock(NODES[i], WALLET);
        steps.push(`Unblock vote ${i + 1} — unblockVotes=${r.unblockVotes}, thresholdReached=${r.thresholdReached}`);
        if (r.thresholdReached) return { passed: false, steps, detail: "Should not unblock before 4 votes" };
      }

      const r4 = sim.voteUnblock(NODES[3], WALLET);
      steps.push(`Unblock vote 4 — thresholdReached=${r4.thresholdReached}`);

      if (!r4.thresholdReached) return { passed: false, steps, detail: "Should unblock on 4th vote" };
      if (sim.isBlocked(WALLET)) return { passed: false, steps, detail: "Wallet should be unblocked" };

      // State fully cleared
      const counts = sim.getVoteCounts(WALLET);
      steps.push(`Vote counts after unblock = ${JSON.stringify(counts)}`);
      if (counts.blockVotes !== 0 || counts.unblockVotes !== 0) {
        return { passed: false, steps, detail: "Vote state should be cleared after unblock" };
      }

      return { passed: true, steps };
    },
  },

  // --- Block rejected after already blocked --------------------------------

  {
    name: "Voting to block an already-blocked wallet throws",
    run() {
      const sim = fresh();
      const steps = [];

      for (const node of NODES.slice(0, 4)) sim.voteBlock(node, WALLET);
      steps.push(`Wallet blocked: ${sim.isBlocked(WALLET)}`);

      const check = assertThrows(
        () => sim.voteBlock(NODES[0], WALLET),
        "already blocked",
      );
      steps.push(`Re-vote throws "already blocked": ${check.ok}`);

      if (!check.ok) return { passed: false, steps, detail: check.detail };
      return { passed: true, steps };
    },
  },

  // --- Unblock on non-blocked wallet ----------------------------------------

  {
    name: "Voting to unblock a non-blocked wallet throws",
    run() {
      const sim = fresh();
      const steps = [];
      const check = assertThrows(
        () => sim.voteUnblock(NODES[0], WALLET),
        "wallet is not blocked",
      );
      steps.push(`Unblock non-blocked wallet throws: ${check.ok}`);
      if (!check.ok) return { passed: false, steps, detail: check.detail };
      return { passed: true, steps };
    },
  },

  // --- Unregistered caller --------------------------------------------------

  {
    name: "Unregistered caller cannot vote",
    run() {
      const sim = fresh();
      const steps = [];
      const check = assertThrows(
        () => sim.voteBlock("ROGUE_NODE", WALLET),
        "not a registered node",
      );
      steps.push(`Unregistered node throws: ${check.ok}`);
      if (!check.ok) return { passed: false, steps, detail: check.detail };
      return { passed: true, steps };
    },
  },

  // --- Node removal ---------------------------------------------------------

  {
    name: "Removed node cannot vote",
    run() {
      const sim = fresh();
      const steps = [];

      sim.removeNode(OWNER, NODES[0]);
      steps.push(`Node 1 removed. Slot count = ${sim.getNodeSlot(NODES[0])}`);

      const check = assertThrows(
        () => sim.voteBlock(NODES[0], WALLET),
        "not a registered node",
      );
      steps.push(`Removed node vote throws: ${check.ok}`);
      if (!check.ok) return { passed: false, steps, detail: check.detail };
      return { passed: true, steps };
    },
  },

  // --- Non-owner cannot register -------------------------------------------

  {
    name: "Non-owner cannot register a node",
    run() {
      const sim = fresh();
      const steps = [];
      const check = assertThrows(
        () => sim.registerNode(NODES[0], NODES[4]),
        "owner only",
      );
      steps.push(`Non-owner register throws: ${check.ok}`);
      if (!check.ok) return { passed: false, steps, detail: check.detail };
      return { passed: true, steps };
    },
  },

  // --- Lower threshold (3-of-4) --------------------------------------------

  {
    name: "Threshold=3 blocks on 3rd vote",
    run() {
      const sim = new BlocklistSim(3, OWNER);
      for (const node of NODES.slice(0, 4)) sim.registerNode(OWNER, node);
      const steps = [];

      sim.voteBlock(NODES[0], WALLET);
      sim.voteBlock(NODES[1], WALLET);
      const r = sim.voteBlock(NODES[2], WALLET);
      steps.push(`3rd vote with threshold=3 — thresholdReached=${r.thresholdReached}`);

      if (!r.thresholdReached) return { passed: false, steps, detail: "Should block on 3rd vote" };
      if (!sim.isBlocked(WALLET)) return { passed: false, steps, detail: "Wallet should be blocked" };

      return { passed: true, steps };
    },
  },

  // --- Multiple independent wallets ----------------------------------------

  {
    name: "Blocking one wallet does not affect another",
    run() {
      const sim = fresh();
      const steps = [];

      for (const node of NODES.slice(0, 4)) sim.voteBlock(node, WALLET);
      steps.push(`WALLET blocked: ${sim.isBlocked(WALLET)}`);
      steps.push(`WALLET2 blocked: ${sim.isBlocked(WALLET2)}`);

      if (!sim.isBlocked(WALLET))  return { passed: false, steps, detail: "WALLET should be blocked" };
      if (sim.isBlocked(WALLET2))  return { passed: false, steps, detail: "WALLET2 should not be blocked" };

      // WALLET2 can accumulate votes independently
      sim.voteBlock(NODES[0], WALLET2);
      const counts = sim.getVoteCounts(WALLET2);
      steps.push(`WALLET2 block votes = ${counts.blockVotes}`);

      if (counts.blockVotes !== 1) return { passed: false, steps, detail: "WALLET2 should have 1 vote" };
      return { passed: true, steps };
    },
  },

  // --- Block, unblock, re-block --------------------------------------------

  {
    name: "Wallet can be re-blocked after unblock",
    run() {
      const sim = fresh();
      const steps = [];

      for (const node of NODES.slice(0, 4)) sim.voteBlock(node, WALLET);
      steps.push(`Blocked: ${sim.isBlocked(WALLET)}`);

      for (const node of NODES.slice(0, 4)) sim.voteUnblock(node, WALLET);
      steps.push(`Unblocked: ${!sim.isBlocked(WALLET)}`);

      for (const node of NODES.slice(0, 4)) sim.voteBlock(node, WALLET);
      steps.push(`Re-blocked: ${sim.isBlocked(WALLET)}`);

      if (!sim.isBlocked(WALLET)) return { passed: false, steps, detail: "Should be blocked again" };
      return { passed: true, steps };
    },
  },

  // --- State snapshot -------------------------------------------------------

  {
    name: "Snapshot shows pending votes correctly",
    run() {
      const sim = fresh();
      const steps = [];

      sim.voteBlock(NODES[0], WALLET);
      sim.voteBlock(NODES[1], WALLET);

      const snap = sim.snapshot();
      steps.push(`Snapshot: ${JSON.stringify(snap, null, 2)}`);

      const pending = snap.pendingBlock[WALLET];
      if (!pending) return { passed: false, steps, detail: "WALLET should appear in pendingBlock" };
      if (pending.votes !== 2) return { passed: false, steps, detail: `Expected 2 pending votes, got ${pending.votes}` };

      return { passed: true, steps };
    },
  },

];

export { SCENARIOS };
