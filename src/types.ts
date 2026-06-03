/** Network identifiers supported by the blocklist. */
export type NetworkId = "algorand" | "voi" | "algorand-testnet" | "voi-testnet";

/** Result returned by vote_block / vote_unblock. */
export interface VoteResult {
  txId: string;
  /** True when this vote pushed the count to the threshold. */
  thresholdReached: boolean;
  network: NetworkId;
}

/** vote counts returned by get_vote_counts / getVoteCounts. */
export interface VoteCounts {
  blockVotes: number;
  unblockVotes: number;
  network: NetworkId;
}

/** Result of the gateway is-blocked check. */
export interface BlockedCheckResult {
  blocked: boolean;
  /** Per-network detail (only populated when both networks are configured). */
  detail?: {
    algorand: boolean;
    voi: boolean;
  };
}
