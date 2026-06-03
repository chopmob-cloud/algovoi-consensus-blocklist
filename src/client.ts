/**
 * Node runner and owner clients for WalletBlocklistConsensus.
 *
 * NodeRunnerClient — used by the four AlgoVoi node runners to cast
 *   block / unblock votes on Algorand or VOI mainnet.
 *
 * OwnerClient — used by the platform operator to register / remove nodes
 *   and manage the deployed contract.
 *
 * Both clients require `algosdk` (peer dep).
 *
 * Usage:
 *   const runner = new NodeRunnerClient({
 *     appId: Number(process.env.BLOCKLIST_APP_ID),
 *     secretKey: hexToUint8Array(process.env.NODE_RUNNER_KEY),
 *     network: "algorand",
 *   });
 *   const result = await runner.voteBlock("WALLET...");
 *   if (result.thresholdReached) console.log("Wallet blocked");
 */

import algosdk from "algosdk";
import { ALGOD_URLS, METHODS, voteBoxRefs, nodeSlotBoxKey } from "./contract.js";
import type { NetworkId, VoteResult, VoteCounts } from "./types.js";

const WAIT_ROUNDS = 5;

// ---------------------------------------------------------------------------
// Shared base
// ---------------------------------------------------------------------------

interface BaseClientOptions {
  appId: number;
  /** 64-byte Ed25519 secret key (from algosdk.mnemonicToSecretKey). */
  secretKey: Uint8Array;
  network?: NetworkId;
  /** Override algod URL. Defaults to the public node for the network. */
  algodUrl?: string;
  algodToken?: string;
}

class BaseClient {
  protected readonly appId: number;
  protected readonly network: NetworkId;
  protected readonly algod: algosdk.Algodv2;
  protected readonly address: string;
  protected readonly signer: algosdk.TransactionSigner;

  constructor(opts: BaseClientOptions) {
    this.appId   = opts.appId;
    this.network = opts.network ?? "algorand";
    const url    = opts.algodUrl ?? ALGOD_URLS[this.network];
    this.algod   = new algosdk.Algodv2(opts.algodToken ?? "", url, "");
    this.address = algosdk.encodeAddress(opts.secretKey.slice(32));
    this.signer  = algosdk.makeBasicAccountTransactionSigner(
      { sk: opts.secretKey, addr: this.address } as algosdk.Account,
    );
  }

  protected async sp(): Promise<algosdk.SuggestedParams> {
    return this.algod.getTransactionParams().do();
  }

  protected async runMethod(
    method: algosdk.ABIMethod,
    methodArgs: algosdk.ABIArgument[],
    boxes: algosdk.BoxReference[],
  ): Promise<algosdk.ABIResult> {
    const atc = new algosdk.AtomicTransactionComposer();
    atc.addMethodCall({
      appID:          this.appId,
      method,
      methodArgs,
      sender:         this.address,
      suggestedParams: await this.sp(),
      signer:         this.signer,
      boxes,
    });
    const result = await atc.execute(this.algod, WAIT_ROUNDS);
    return result.methodResults[0];
  }
}

// ---------------------------------------------------------------------------
// NodeRunnerClient
// ---------------------------------------------------------------------------

export class NodeRunnerClient extends BaseClient {
  /**
   * Cast a block vote for `walletAddress`.
   * Idempotent — re-voting by the same node has no effect on the bitmask.
   * Throws if the caller is not a registered node or the wallet is already blocked.
   */
  async voteBlock(walletAddress: string): Promise<VoteResult> {
    validateAddress(walletAddress);
    const result = await this.runMethod(
      METHODS.voteBlock,
      [walletAddress],
      voteBoxRefs(this.appId, this.address, walletAddress),
    );
    return {
      txId:             result.txID,
      thresholdReached: result.returnValue as boolean,
      network:          this.network,
    };
  }

  /**
   * Cast an unblock vote for a currently blocked `walletAddress`.
   * Throws if the wallet is not blocked or the caller is not a registered node.
   */
  async voteUnblock(walletAddress: string): Promise<VoteResult> {
    validateAddress(walletAddress);
    const result = await this.runMethod(
      METHODS.voteUnblock,
      [walletAddress],
      voteBoxRefs(this.appId, this.address, walletAddress),
    );
    return {
      txId:             result.txID,
      thresholdReached: result.returnValue as boolean,
      network:          this.network,
    };
  }
}

// ---------------------------------------------------------------------------
// OwnerClient
// ---------------------------------------------------------------------------

export class OwnerClient extends BaseClient {
  /**
   * Register a node runner address. Returns the assigned slot number (1–64).
   * Owner only.
   */
  async registerNode(nodeAddress: string): Promise<{ txId: string; slot: number }> {
    validateAddress(nodeAddress);
    const result = await this.runMethod(
      METHODS.registerNode,
      [nodeAddress],
      [{ appIndex: this.appId, name: nodeSlotBoxKey(nodeAddress) }],
    );
    return {
      txId: result.txID,
      slot: Number(result.returnValue as bigint),
    };
  }

  /** Remove a node runner. Owner only. */
  async removeNode(nodeAddress: string): Promise<string> {
    validateAddress(nodeAddress);
    const result = await this.runMethod(
      METHODS.removeNode,
      [nodeAddress],
      [{ appIndex: this.appId, name: nodeSlotBoxKey(nodeAddress) }],
    );
    return result.txID;
  }

  /** Update the vote threshold. Owner only. */
  async updateThreshold(newThreshold: number): Promise<string> {
    const result = await this.runMethod(
      METHODS.updateThreshold,
      [newThreshold],
      [],
    );
    return result.txID;
  }

  /** Transfer contract ownership. Owner only. */
  async transferOwnership(newOwnerAddress: string): Promise<string> {
    validateAddress(newOwnerAddress);
    const result = await this.runMethod(
      METHODS.transferOwnership,
      [newOwnerAddress],
      [],
    );
    return result.txID;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function validateAddress(address: string): void {
  if (!algosdk.isValidAddress(address)) {
    throw new Error(`Invalid Algorand/VOI address: ${address}`);
  }
}
