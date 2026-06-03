# algovoi-consensus-blocklist

On-chain consensus wallet blocklist for **Algorand** and **VOI** networks.

Requires **4 registered node runners** to independently vote before a wallet address is blocked or unblocked. Built with [algopy](https://github.com/algorandfoundation/puya) (Algorand Python) and compiled to AVM bytecode — identical contract deployed on both networks.

---

## How it works

Each registered node runner holds a unique bit position in a `UInt64` bitmask. When a node votes to block a wallet, their bit is set. Once 4 distinct bits are set, the wallet is marked as blocked on-chain. Unblocking follows the same 4-of-N process.

```
Node 1 votes block  →  bitmask: 0001  (1/4)
Node 2 votes block  →  bitmask: 0011  (2/4)
Node 3 votes block  →  bitmask: 0111  (3/4)
Node 4 votes block  →  bitmask: 1111  (4/4) ✓ wallet blocked
```

Key properties:

- **Consensus required** — no single node can block or unblock a wallet alone
- **Up to 64 node runners** — each assigned a permanent slot index (1–64)
- **Idempotent votes** — re-voting by the same node is a no-op (bitmask OR)
- **Symmetric unblock** — restoring a wallet requires the same 4-of-N consensus
- **Cross-network** — a wallet blocked on either Algorand or VOI is treated as blocked on both (same 32-byte key space)

---

## Repository structure

```
contracts/blocklist_consensus/
    contract.py          # algopy ARC-4 smart contract source
    client.py            # NodeRunnerClient + OwnerClient (Python)
    deploy.py            # deployment and node registration CLI
    tests/
        test_blocklist.py        # algopy unit tests
        test_gateway_service.py  # gateway service mocked tests

on_chain_blocklist.py    # async gateway query service (Python)

src/                     # TypeScript package
    types.ts             # shared types
    contract.ts          # ABI methods + box key helpers
    client.ts            # NodeRunnerClient + OwnerClient
    gateway.ts           # OnChainBlocklistGateway (fetch-based, no algosdk dep)
    index.ts             # public exports

tests/
    gateway.test.ts      # vitest tests

package.json
tsconfig.json
```

---

## Contract interface

### Deploy

```python
deploy(threshold: uint64)
```

Creates the contract. `threshold` is typically `4`. Called once by the owner at deploy time.

### Node management (owner only)

```python
register_node(node: address) -> uint64   # returns assigned slot (1–64)
remove_node(node: address)
update_threshold(new_threshold: uint64)
transfer_ownership(new_owner: address)
```

### Voting (registered node runners only)

```python
vote_block(wallet: address) -> bool      # True when threshold reached
vote_unblock(wallet: address) -> bool    # True when threshold reached
```

### Readonly queries

```python
is_blocked(wallet: address) -> bool
get_vote_counts(wallet: address) -> (uint64, uint64)   # (block_votes, unblock_votes)
get_node_slot(node: address) -> uint64                  # 0 = not registered
```

---

## Box storage layout

Each entry is stored as an Algorand box: `prefix (2 bytes) + address (32 bytes) → UInt64 (8 bytes)`

| Prefix | Contents |
|--------|----------|
| `ns`   | Node slot index (1-based) |
| `bv`   | Block-vote bitmask |
| `uv`   | Unblock-vote bitmask |
| `bl`   | Blocked flag (nonzero = blocked) |

---

## Getting started

### Prerequisites

```bash
pip install algorand-python algosdk algokit-utils
algokit init  # or: pip install algokit
```

### Compile

```bash
algokit compile contracts/blocklist_consensus/contract.py
# Outputs: contracts/blocklist_consensus/artifacts/
#   WalletBlocklistConsensus.approval.teal
#   WalletBlocklistConsensus.clear.teal
#   WalletBlocklistConsensus.arc56.json
```

### Deploy

```bash
# Algorand mainnet — deploy and register all 4 node runners
python -m contracts.blocklist_consensus.deploy \
  --network algorand \
  --threshold 4 \
  --owner-mnemonic "$OWNER_MNEMONIC" \
  --nodes "NODEADDR1,NODEADDR2,NODEADDR3,NODEADDR4"

# VOI mainnet — same contract, different network
python -m contracts.blocklist_consensus.deploy \
  --network voi \
  --threshold 4 \
  --owner-mnemonic "$OWNER_MNEMONIC" \
  --nodes "NODEADDR1,NODEADDR2,NODEADDR3,NODEADDR4"
```

Supported `--network` values: `algorand`, `voi`, `algorand-testnet`, `voi-testnet`

### Vote to block a wallet (node runner)

```python
from contracts.blocklist_consensus.client import NodeRunnerClient

# Algorand
runner = NodeRunnerClient(
    app_id=int(os.environ["BLOCKLIST_APP_ID"]),
    node_private_key=os.environ["NODE_RUNNER_KEY"],
    network="algorand",
)
result = runner.vote_block("WALLETADDRESS...")
print(result)
# {"tx_id": "...", "threshold_reached": False, "network": "algorand"}

# VOI
runner_voi = NodeRunnerClient(
    app_id=int(os.environ["BLOCKLIST_VOI_APP_ID"]),
    node_private_key=os.environ["NODE_RUNNER_KEY"],
    network="voi",
)
```

### Gateway integration

`on_chain_blocklist.py` provides an async service for payment gateways. It checks both networks in parallel and fails open (returns `False`) if a node is unreachable.

```python
from on_chain_blocklist import is_wallet_blocked

blocked = await is_wallet_blocked(payer_address)
if blocked:
    raise HTTPException(403, detail="wallet_blocked")
```

**Environment variables:**

| Variable | Description | Default |
|----------|-------------|---------|
| `BLOCKLIST_APP_ID` | Algorand mainnet app ID (`0` = disabled) | `0` |
| `BLOCKLIST_VOI_APP_ID` | VOI mainnet app ID (`0` = disabled) | `0` |
| `BLOCKLIST_ALGOD_URL` | Algorand algod endpoint | `https://mainnet-api.algonode.cloud` |
| `BLOCKLIST_VOI_ALGOD_URL` | VOI algod endpoint | `https://mainnet-api.voi.nodely.dev` |
| `BLOCKLIST_CACHE_SECONDS` | In-process cache TTL | `30` |

---

## TypeScript

### Install

```bash
npm install @algovoi/consensus-blocklist
# algosdk is only required for NodeRunnerClient / OwnerClient
npm install algosdk
```

### Gateway query (no algosdk required)

```typescript
import { OnChainBlocklistGateway } from "@algovoi/consensus-blocklist/gateway";

const gateway = new OnChainBlocklistGateway({
  algorand: { appId: Number(process.env.BLOCKLIST_APP_ID) },
  voi:      { appId: Number(process.env.BLOCKLIST_VOI_APP_ID) },
  cacheTtlMs: 30_000,
});

const blocked = await gateway.isBlocked("WALLETADDRESS...");
if (blocked) throw new Error("wallet_blocked");
```

### Node runner voting

```typescript
import { NodeRunnerClient } from "@algovoi/consensus-blocklist/client";
import algosdk from "algosdk";

const { sk } = algosdk.mnemonicToSecretKey(process.env.NODE_RUNNER_MNEMONIC!);

const runner = new NodeRunnerClient({
  appId:     Number(process.env.BLOCKLIST_APP_ID),
  secretKey: sk,
  network:   "algorand",
});

const result = await runner.voteBlock("WALLETADDRESS...");
console.log(result);
// { txId: "...", thresholdReached: false, network: "algorand" }
```

### Build and test

```bash
npm ci
npm test          # vitest
npm run typecheck # tsc --noEmit
npm run build     # tsup → dist/
```

---

## Running the Python tests

```bash
# Contract unit tests (algopy testing framework)
pytest contracts/blocklist_consensus/tests/test_blocklist.py -v

# Gateway service tests (mocked — no live node required)
pytest contracts/blocklist_consensus/tests/test_gateway_service.py -v
```

---

## Security notes

- **No single point of control** — the threshold enforces that no individual node can block a wallet unilaterally
- **Slot retirement** — removing a node retires their slot permanently; the index is never reused, preventing replay of old vote bitmasks
- **Fail-open gateway** — an unreachable contract never halts legitimate payments
- **Cross-network policy** — blocking on either Algorand or VOI is treated as a block on both; a sanctioned actor cannot evade enforcement by switching networks
- **Readonly queries are free** — `is_blocked` uses Algorand box reads (direct REST call), not a signed transaction; no fees on the query path

---

## Networks

| Network | Algod endpoint |
|---------|---------------|
| Algorand mainnet | `https://mainnet-api.algonode.cloud` |
| Algorand testnet | `https://testnet-api.algonode.cloud` |
| VOI mainnet | `https://mainnet-api.voi.nodely.dev` |
| VOI testnet | `https://testnet-api.voi.nodely.dev` |

---

## Part of the AlgoVoi platform

[AlgoVoi](https://algovoi.co.uk) is a multi-chain x402 payment gateway. This contract is the on-chain enforcement layer for the gateway's compliance blocklist, complementing off-chain sanctions screening (OFSI / OFAC SDN / EU Consolidated).
