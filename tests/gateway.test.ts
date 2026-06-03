import { describe, it, expect, vi, beforeEach } from "vitest";
import { OnChainBlocklistGateway } from "../src/gateway.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a base64-encoded 8-byte big-endian UInt64. */
function uint64B64(value: number): string {
  const buf = new Uint8Array(8);
  const view = new DataView(buf.buffer);
  view.setBigUint64(0, BigInt(value), false);
  return btoa(String.fromCharCode(...buf));
}

const VALID_ADDR = "7777777777777777777777777777777777777777777777777774MSJUVU";

function makeFetch(responses: Array<{ status: number; body?: unknown }>) {
  let call = 0;
  return vi.fn(async () => {
    const r = responses[Math.min(call++, responses.length - 1)];
    return {
      status: r.status,
      ok: r.status >= 200 && r.status < 300,
      json: async () => r.body,
    } as Response;
  });
}

// ---------------------------------------------------------------------------
// isBlocked
// ---------------------------------------------------------------------------

describe("OnChainBlocklistGateway.isBlocked", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("returns false when no networks are configured", async () => {
    const gw = new OnChainBlocklistGateway({});
    expect(await gw.isBlocked(VALID_ADDR)).toBe(false);
  });

  it("returns false when the blocked box is absent (404)", async () => {
    vi.stubGlobal("fetch", makeFetch([{ status: 404 }]));
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 } });
    expect(await gw.isBlocked(VALID_ADDR)).toBe(false);
  });

  it("returns true when the blocked box value is 1", async () => {
    vi.stubGlobal("fetch", makeFetch([{ status: 200, body: { value: uint64B64(1) } }]));
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 } });
    expect(await gw.isBlocked(VALID_ADDR)).toBe(true);
  });

  it("returns false when blocked box value is 0", async () => {
    vi.stubGlobal("fetch", makeFetch([{ status: 200, body: { value: uint64B64(0) } }]));
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 } });
    expect(await gw.isBlocked(VALID_ADDR)).toBe(false);
  });

  it("returns true when blocked only on VOI (Algorand 404, VOI blocked)", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch([
        { status: 404 },                                       // Algorand — not blocked
        { status: 200, body: { value: uint64B64(1) } },        // VOI — blocked
      ]),
    );
    const gw = new OnChainBlocklistGateway({
      algorand: { appId: 111 },
      voi:      { appId: 222 },
    });
    expect(await gw.isBlocked(VALID_ADDR)).toBe(true);
  });

  it("returns true when blocked only on Algorand (VOI 404)", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch([
        { status: 200, body: { value: uint64B64(1) } },        // Algorand — blocked
        { status: 404 },                                       // VOI — not blocked
      ]),
    );
    const gw = new OnChainBlocklistGateway({
      algorand: { appId: 111 },
      voi:      { appId: 222 },
    });
    expect(await gw.isBlocked(VALID_ADDR)).toBe(true);
  });

  it("fails open when fetch throws (returns false, does not throw)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 } });
    await expect(gw.isBlocked(VALID_ADDR)).resolves.toBe(false);
  });

  it("uses cache on second call (fetch called only once)", async () => {
    const mockFetch = makeFetch([{ status: 200, body: { value: uint64B64(1) } }]);
    vi.stubGlobal("fetch", mockFetch);
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 }, cacheTtlMs: 60_000 });
    await gw.isBlocked(VALID_ADDR);
    await gw.isBlocked(VALID_ADDR);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("re-fetches after cache is invalidated", async () => {
    const mockFetch = makeFetch([
      { status: 200, body: { value: uint64B64(1) } },
      { status: 404 },
    ]);
    vi.stubGlobal("fetch", mockFetch);
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 }, cacheTtlMs: 60_000 });
    expect(await gw.isBlocked(VALID_ADDR)).toBe(true);
    gw.invalidate(VALID_ADDR);
    expect(await gw.isBlocked(VALID_ADDR)).toBe(false);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// getVoteCounts
// ---------------------------------------------------------------------------

describe("OnChainBlocklistGateway.getVoteCounts", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("returns zero counts when network is not configured", async () => {
    const gw = new OnChainBlocklistGateway({});
    const counts = await gw.getVoteCounts(VALID_ADDR, "algorand");
    expect(counts).toEqual({ blockVotes: 0, unblockVotes: 0, network: "algorand" });
  });

  it("decodes bitmask popcount correctly (bv=0b1111=4, uv=0b0011=2)", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch([
        { status: 200, body: { value: uint64B64(0b1111) } },   // block votes
        { status: 200, body: { value: uint64B64(0b0011) } },   // unblock votes
      ]),
    );
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 } });
    const counts = await gw.getVoteCounts(VALID_ADDR, "algorand");
    expect(counts.blockVotes).toBe(4);
    expect(counts.unblockVotes).toBe(2);
    expect(counts.network).toBe("algorand");
  });

  it("handles 404 boxes as zero votes", async () => {
    vi.stubGlobal("fetch", makeFetch([{ status: 404 }, { status: 404 }]));
    const gw = new OnChainBlocklistGateway({ algorand: { appId: 999 } });
    const counts = await gw.getVoteCounts(VALID_ADDR);
    expect(counts.blockVotes).toBe(0);
    expect(counts.unblockVotes).toBe(0);
  });
});
