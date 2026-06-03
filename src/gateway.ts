/**
 * Gateway query service for WalletBlocklistConsensus.
 *
 * Reads on-chain box state directly via the algod REST API — no algosdk
 * dependency, no transaction fees. Uses the native fetch API (Node 18+
 * and all modern browsers).
 *
 * Both Algorand and VOI are checked in parallel. A wallet blocked on
 * either network is treated as blocked overall — the same 32-byte key
 * space is shared across both chains.
 *
 * Fail-open: an unreachable contract returns false so legitimate payments
 * are never halted by an infra outage.
 *
 * Usage (Node.js / server):
 *   const gateway = new OnChainBlocklistGateway({
 *     algorand: { appId: Number(process.env.BLOCKLIST_APP_ID) },
 *     voi:      { appId: Number(process.env.BLOCKLIST_VOI_APP_ID) },
 *   });
 *   const blocked = await gateway.isBlocked("WALLET...");
 *
 * Usage (minimal — single network):
 *   const gateway = new OnChainBlocklistGateway({
 *     algorand: { appId: 12345678 },
 *   });
 */

import type { BlockedCheckResult, VoteCounts, NetworkId } from "./types.js";
import { ALGOD_URLS } from "./contract.js";

// ---------------------------------------------------------------------------
// Box key construction (no algosdk — pure TypeScript)
// ---------------------------------------------------------------------------

const PREFIX_BLOCKED       = new Uint8Array([0x62, 0x6c]); // "bl"
const PREFIX_BLOCK_VOTES   = new Uint8Array([0x62, 0x76]); // "bv"
const PREFIX_UNBLOCK_VOTES = new Uint8Array([0x75, 0x76]); // "uv"

/**
 * Decode a base32+checksum Algorand/VOI address to its 32-byte public key.
 * Implements the Algorand address format without algosdk.
 */
function decodeAddress(address: string): Uint8Array {
  // Algorand addresses are base32-encoded (no padding) 32-byte pubkey + 4-byte checksum
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = address.toUpperCase().replace(/=+$/, "");
  let bits = 0, value = 0;
  const output: number[] = [];
  for (const char of clean) {
    const idx = alphabet.indexOf(char);
    if (idx === -1) throw new Error(`Invalid address character: ${char}`);
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      output.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  if (output.length < 32) throw new Error(`Address too short: ${address}`);
  return new Uint8Array(output.slice(0, 32)); // strip 4-byte checksum
}

function buildBoxKey(prefix: Uint8Array, walletAddress: string): Uint8Array {
  const addrBytes = decodeAddress(walletAddress);
  const key = new Uint8Array(prefix.length + addrBytes.length);
  key.set(prefix, 0);
  key.set(addrBytes, prefix.length);
  return key;
}

function toBase64(bytes: Uint8Array): string {
  // Works in Node 18+ and all browsers
  return btoa(String.fromCharCode(...bytes));
}

function decodeUint64(base64Value: string): bigint {
  const bytes = Uint8Array.from(atob(base64Value), c => c.charCodeAt(0));
  let result = 0n;
  for (const byte of bytes) result = (result << 8n) | BigInt(byte);
  return result;
}

function popcount(n: bigint): number {
  let count = 0;
  while (n > 0n) { count += Number(n & 1n); n >>= 1n; }
  return count;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface NetworkConfig {
  appId: number;
  /** Defaults to the public node URL for the network. */
  algodUrl?: string;
  algodToken?: string;
}

export interface GatewayConfig {
  algorand?: NetworkConfig;
  voi?: NetworkConfig;
  /** In-process cache TTL in milliseconds (default: 30 000). */
  cacheTtlMs?: number;
}

// ---------------------------------------------------------------------------
// Simple TTL cache
// ---------------------------------------------------------------------------

interface CacheEntry { blocked: boolean; at: number }

class BlockedCache {
  private readonly ttl: number;
  private readonly store = new Map<string, CacheEntry>();

  constructor(ttlMs: number) { this.ttl = ttlMs; }

  get(key: string): boolean | undefined {
    const entry = this.store.get(key);
    if (!entry) return undefined;
    if (Date.now() - entry.at > this.ttl) { this.store.delete(key); return undefined; }
    return entry.blocked;
  }

  set(key: string, blocked: boolean): void {
    this.store.set(key, { blocked, at: Date.now() });
  }

  invalidate(key: string): void { this.store.delete(key); }
}

// ---------------------------------------------------------------------------
// OnChainBlocklistGateway
// ---------------------------------------------------------------------------

export class OnChainBlocklistGateway {
  private readonly algorand?: NetworkConfig;
  private readonly voi?: NetworkConfig;
  private readonly cache: BlockedCache;

  constructor(config: GatewayConfig) {
    this.algorand = config.algorand?.appId ? config.algorand : undefined;
    this.voi      = config.voi?.appId      ? config.voi      : undefined;
    this.cache    = new BlockedCache(config.cacheTtlMs ?? 30_000);
  }

  /**
   * Returns true if the wallet is blocked on Algorand OR VOI.
   * Fail-open: returns false on any network error.
   */
  async isBlocked(walletAddress: string): Promise<boolean> {
    const cached = this.cache.get(walletAddress);
    if (cached !== undefined) return cached;

    const checks: Promise<boolean>[] = [];
    if (this.algorand) checks.push(this.checkNetwork(walletAddress, "algorand", this.algorand));
    if (this.voi)      checks.push(this.checkNetwork(walletAddress, "voi",      this.voi));

    if (checks.length === 0) return false;

    const results = await Promise.all(checks);
    const blocked = results.some(Boolean);
    this.cache.set(walletAddress, blocked);
    return blocked;
  }

  /**
   * Returns detailed per-network block and vote counts for a wallet.
   * Useful for admin/audit surfaces.
   */
  async getVoteCounts(walletAddress: string, network: NetworkId = "algorand"): Promise<VoteCounts> {
    const cfg = network === "voi" ? this.voi : this.algorand;
    if (!cfg) return { blockVotes: 0, unblockVotes: 0, network };

    const algodUrl = (cfg.algodUrl ?? ALGOD_URLS[network]).replace(/\/$/, "");

    const [bv, uv] = await Promise.all([
      this.readBox(algodUrl, cfg.appId, buildBoxKey(PREFIX_BLOCK_VOTES,   walletAddress), cfg.algodToken),
      this.readBox(algodUrl, cfg.appId, buildBoxKey(PREFIX_UNBLOCK_VOTES, walletAddress), cfg.algodToken),
    ]);

    return {
      blockVotes:   popcount(bv ?? 0n),
      unblockVotes: popcount(uv ?? 0n),
      network,
    };
  }

  /** Evict a wallet from the local cache. */
  invalidate(walletAddress: string): void {
    this.cache.invalidate(walletAddress);
  }

  // ------------------------------------------------------------------
  // Private
  // ------------------------------------------------------------------

  private async checkNetwork(
    walletAddress: string,
    network: NetworkId,
    cfg: NetworkConfig,
  ): Promise<boolean> {
    try {
      const algodUrl = (cfg.algodUrl ?? ALGOD_URLS[network]).replace(/\/$/, "");
      const raw = await this.readBox(
        algodUrl, cfg.appId,
        buildBoxKey(PREFIX_BLOCKED, walletAddress),
        cfg.algodToken,
      );
      return raw !== null && raw > 0n;
    } catch {
      // fail-open per network
      return false;
    }
  }

  /**
   * Read a box value via GET /v2/applications/{appId}/box?name=b64:{key}.
   * Returns the decoded UInt64, or null if the box does not exist.
   */
  private async readBox(
    algodUrl: string,
    appId: number,
    boxKey: Uint8Array,
    algodToken?: string,
  ): Promise<bigint | null> {
    const b64Key = toBase64(boxKey);
    const url = `${algodUrl}/v2/applications/${appId}/box?name=b64:${encodeURIComponent(b64Key)}`;
    const headers: Record<string, string> = {};
    if (algodToken) headers["X-Algo-API-Token"] = algodToken;

    const resp = await fetch(url, { headers });
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`algod box read failed: ${resp.status} ${url}`);

    const data = await resp.json() as { value: string };
    return decodeUint64(data.value);
  }
}
