/**
 * ABI method definitions and box-key helpers for WalletBlocklistConsensus.
 *
 * Import this module without algosdk — it only constructs plain objects
 * that algosdk methods accept.
 */

import algosdk from "algosdk";
import type { NetworkId } from "./types.js";

// ---------------------------------------------------------------------------
// Network constants
// ---------------------------------------------------------------------------

export const ALGOD_URLS: Record<NetworkId, string> = {
  algorand:          "https://mainnet-api.algonode.cloud",
  voi:               "https://mainnet-api.voi.nodely.dev",
  "algorand-testnet": "https://testnet-api.algonode.cloud",
  "voi-testnet":     "https://testnet-api.voi.nodely.dev",
};

// ---------------------------------------------------------------------------
// ABI methods
// ---------------------------------------------------------------------------

export const METHODS = {
  deploy: new algosdk.ABIMethod({
    name: "deploy",
    args: [{ type: "uint64", name: "threshold" }],
    returns: { type: "void" },
  }),

  registerNode: new algosdk.ABIMethod({
    name: "register_node",
    args: [{ type: "address", name: "node" }],
    returns: { type: "uint64" },
  }),

  removeNode: new algosdk.ABIMethod({
    name: "remove_node",
    args: [{ type: "address", name: "node" }],
    returns: { type: "void" },
  }),

  voteBlock: new algosdk.ABIMethod({
    name: "vote_block",
    args: [{ type: "address", name: "wallet" }],
    returns: { type: "bool" },
  }),

  voteUnblock: new algosdk.ABIMethod({
    name: "vote_unblock",
    args: [{ type: "address", name: "wallet" }],
    returns: { type: "bool" },
  }),

  isBlocked: new algosdk.ABIMethod({
    name: "is_blocked",
    args: [{ type: "address", name: "wallet" }],
    returns: { type: "bool" },
  }),

  getVoteCounts: new algosdk.ABIMethod({
    name: "get_vote_counts",
    args: [{ type: "address", name: "wallet" }],
    returns: { type: "(uint64,uint64)" },
  }),

  updateThreshold: new algosdk.ABIMethod({
    name: "update_threshold",
    args: [{ type: "uint64", name: "new_threshold" }],
    returns: { type: "void" },
  }),

  transferOwnership: new algosdk.ABIMethod({
    name: "transfer_ownership",
    args: [{ type: "address", name: "new_owner" }],
    returns: { type: "void" },
  }),
} as const;

// ---------------------------------------------------------------------------
// Box key helpers
// ---------------------------------------------------------------------------

const PREFIX_NODE_SLOT    = new TextEncoder().encode("ns");
const PREFIX_BLOCK_VOTES  = new TextEncoder().encode("bv");
const PREFIX_UNBLOCK_VOTES = new TextEncoder().encode("uv");
const PREFIX_BLOCKED      = new TextEncoder().encode("bl");

function boxKey(prefix: Uint8Array, address: string): Uint8Array {
  const addrBytes = algosdk.decodeAddress(address).publicKey;
  const key = new Uint8Array(prefix.length + addrBytes.length);
  key.set(prefix, 0);
  key.set(addrBytes, prefix.length);
  return key;
}

export function nodeSlotBoxKey(nodeAddress: string): Uint8Array {
  return boxKey(PREFIX_NODE_SLOT, nodeAddress);
}

export function blockVotesBoxKey(walletAddress: string): Uint8Array {
  return boxKey(PREFIX_BLOCK_VOTES, walletAddress);
}

export function unblockVotesBoxKey(walletAddress: string): Uint8Array {
  return boxKey(PREFIX_UNBLOCK_VOTES, walletAddress);
}

export function blockedBoxKey(walletAddress: string): Uint8Array {
  return boxKey(PREFIX_BLOCKED, walletAddress);
}

/** All four box references needed for a vote call. */
export function voteBoxRefs(
  appId: number,
  callerAddress: string,
  walletAddress: string,
): algosdk.BoxReference[] {
  return [
    { appIndex: appId, name: nodeSlotBoxKey(callerAddress) },
    { appIndex: appId, name: blockVotesBoxKey(walletAddress) },
    { appIndex: appId, name: unblockVotesBoxKey(walletAddress) },
    { appIndex: appId, name: blockedBoxKey(walletAddress) },
  ];
}
