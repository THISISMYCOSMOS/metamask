# Research evidence

This directory stores claim-bounded evidence for the research paper. It is not
a wallet-native enforcement log and it must not contain credentials or RPC
URLs.

## Layout

- `inputs/`: manually curated historical records with explicit provenance.
- `sources/`: strict exporter inputs assembled from validated implementation
  outputs.
- `bundles/`: common-schema, hash-bound evidence bundles.
- `benchmark/`: live Gemini prediction records and the reproducibility/run
  manifest.

## Current cases

| Bundle | Decision | Broadcast | Evidence boundary |
| --- | --- | --- | --- |
| `offline-g3-reject` | reject | no | Pinned-fork G3 candidate and deterministic evaluator |
| `offline-benign-accept` | accept | no | Constructed counterfactual control, not on-chain |
| `live-floor-accept` | accept | yes | Live Gemini approval, application gate, MetaMask Agent Wallet MFA/broadcast, public-chain receipt |
| `live-floor-preflight-reject` | reject | no | Same approved Agent Wallet policy; live Sepolia preflight and unchanged CLI request list |

Read each bundle's `claims` and `limitations` before citing it. In particular,
`accepted` means the policy allowed a candidate; it is not synonymous with a
confirmed transaction. `broadcastAttempted` and `receiptStatus` state the
execution result separately.

The live rejection used the same approved policy and Agent Wallet as the live
accept. A 0.5 USDC candidate from the remaining 0.9 USDC would leave 0.4 USDC,
below the 0.5 USDC floor. The `--broadcast` invocation failed closed before the
CLI sender, the Agent Wallet request-id set remained unchanged, and no
transaction hash was created.

The live accept is a direct ERC-20 transfer from the active Agent Wallet, not a
signed Delegation Framework redemption. The application gate enforced the
approved floor; Agent Wallet separately enforced Guard Mode and MFA. Transaction
`0xaf7566c59d0b10c3983f2478088ac31df165b1acaf1b6084acacd96d08d4f500`
left the wallet with exactly 0.9 Sepolia USDC.
