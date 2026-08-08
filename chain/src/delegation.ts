// docs/caveat-encoding.md 정본을 그대로 구현.
// terms는 전부 packed bytes(abi.encodePacked 상당)다. ABI 인코딩이 아니다.
import {
  concatHex,
  encodePacked,
  keccak256,
  pad,
  toBytes,
  toHex,
  type Address,
  type Hex,
  type LocalAccount,
} from "viem";
import { DELEGATION_DOMAIN_NAME, DELEGATION_DOMAIN_VERSION, ROOT_AUTHORITY } from "./config.js";

// ── 고정 caveat 순서 (docs/caveat-encoding.md, 변경 금지) ───────────────
export const CAVEAT_ORDER = [
  "AllowedTargetsEnforcer",
  "AllowedMethodsEnforcer",
  "ValueLteEnforcer",
  "TimestampEnforcer",
  "ERC20PeriodTransferEnforcer",
  "ERC20BalanceChangeEnforcer",
] as const;

// ── 1. AllowedTargetsEnforcer — 20B × N ─────────────────────────────────
export function encodeAllowedTargets(targets: readonly Address[]): Hex {
  const terms = concatHex(targets.map((t) => pad(t, { size: 20 })));
  assertByteLength(terms, targets.length * 20, "AllowedTargetsEnforcer");
  return terms;
}

// ── 2. AllowedMethodsEnforcer — 4B × N ──────────────────────────────────
export function encodeAllowedMethods(selectors: readonly Hex[]): Hex {
  const terms = concatHex(selectors.map((s) => validateSelector(s)));
  assertByteLength(terms, selectors.length * 4, "AllowedMethodsEnforcer");
  return terms;
}

// ── 3. ValueLteEnforcer — 32B ────────────────────────────────────────────
export function encodeValueLte(maxValue: bigint): Hex {
  const terms = pad(toHex(maxValue), { size: 32 });
  assertByteLength(terms, 32, "ValueLteEnforcer");
  return terms;
}

// ── 4. TimestampEnforcer — 32B (16B after + 16B before) ─────────────────
export function encodeTimestamp(after: bigint, before: bigint): Hex {
  const afterBytes = pad(toHex(after), { size: 16 });
  const beforeBytes = pad(toHex(before), { size: 16 });
  const terms = concatHex([afterBytes, beforeBytes]);
  assertByteLength(terms, 32, "TimestampEnforcer");
  return terms;
}

// ── 5. ERC20BalanceChangeEnforcer — 73B ─────────────────────────────────
// [enforceDecrease(1B)][token(20B)][recipient(20B)][amount(uint256,32B)]
export function encodeERC20BalanceChange(
  enforceDecrease: boolean,
  token: Address,
  recipient: Address,
  amount: bigint,
): Hex {
  const terms = concatHex([
    enforceDecrease ? "0x01" : "0x00",
    pad(token, { size: 20 }),
    pad(recipient, { size: 20 }),
    pad(toHex(amount), { size: 32 }),
  ]);
  assertByteLength(terms, 73, "ERC20BalanceChangeEnforcer");
  return terms;
}

// ── 6. ERC20PeriodTransferEnforcer — 116B ───────────────────────────────
// [token(20B)][periodAmount(32B)][periodDuration(32B)][startDate(32B)]
export function encodeERC20PeriodTransfer(
  token: Address,
  periodAmount: bigint,
  periodDuration: bigint,
  startDate: bigint,
): Hex {
  const terms = concatHex([
    pad(token, { size: 20 }),
    pad(toHex(periodAmount), { size: 32 }),
    pad(toHex(periodDuration), { size: 32 }),
    pad(toHex(startDate), { size: 32 }),
  ]);
  assertByteLength(terms, 116, "ERC20PeriodTransferEnforcer");
  return terms;
}

function validateSelector(selector: Hex): Hex {
  const bytes = toBytes(selector);
  if (bytes.length !== 4) {
    throw new Error(`selector must be 4 bytes, got ${bytes.length}: ${selector}`);
  }
  return selector;
}

function assertByteLength(terms: Hex, expected: number, name: string): void {
  const actual = toBytes(terms).length;
  if (actual !== expected) {
    throw new Error(`${name} terms length mismatch: expected ${expected}B, got ${actual}B (${terms})`);
  }
}

// ── Delegation / Caveat 구조 ─────────────────────────────────────────────
export interface CaveatInput {
  enforcer: Address;
  terms: Hex;
}

export interface DelegationInput {
  delegate: Address;
  delegator: Address;
  authority: Hex;
  caveats: CaveatInput[];
  salt: bigint;
}

export interface SignedDelegation extends DelegationInput {
  signature: Hex;
}

const DELEGATION_EIP712_TYPES = {
  Caveat: [
    { name: "enforcer", type: "address" },
    { name: "terms", type: "bytes" },
  ],
  Delegation: [
    { name: "delegate", type: "address" },
    { name: "delegator", type: "address" },
    { name: "authority", type: "bytes32" },
    { name: "caveats", type: "Caveat[]" },
    { name: "salt", type: "uint256" },
  ],
} as const;

export function buildRootDelegation(
  delegate: Address,
  delegator: Address,
  caveats: CaveatInput[],
  salt = 0n,
): DelegationInput {
  return {
    delegate,
    delegator,
    authority: ROOT_AUTHORITY,
    caveats,
    salt,
  };
}

/**
 * owner EOA(anvil key #1)로 delegation에 EIP-712 서명한다.
 * delegator가 컨트랙트(HybridDeleGator)이므로 ERC-1271로 검증되지만, 서명 길이가
 * 65B이면 ECDSA로 owner()와 비교하는 경로를 타므로 평범한 secp256k1 서명이면 된다.
 */
export async function signDelegation(
  account: LocalAccount,
  delegationManagerAddress: Address,
  delegation: DelegationInput,
): Promise<SignedDelegation> {
  const signature = await account.signTypedData({
    domain: {
      name: DELEGATION_DOMAIN_NAME,
      version: DELEGATION_DOMAIN_VERSION,
      chainId: 1,
      verifyingContract: delegationManagerAddress,
    },
    types: DELEGATION_EIP712_TYPES,
    primaryType: "Delegation",
    message: {
      delegate: delegation.delegate,
      delegator: delegation.delegator,
      authority: delegation.authority,
      caveats: delegation.caveats.map((c) => ({ enforcer: c.enforcer, terms: c.terms })),
      salt: delegation.salt,
    },
  });

  if (toBytes(signature).length !== 65) {
    throw new Error(`expected 65-byte ECDSA signature, got ${toBytes(signature).length}B`);
  }

  return { ...delegation, signature };
}

/**
 * DelegationManager.redeemDelegations의 permissionContext 인코딩.
 * abi.decode(_permissionContexts[i], (Delegation[])) 이므로 abi.encode([delegation])와 동일.
 * viem 상위 코드에서 encodeAbiParameters로 만든다 (deploy/negative-control에서 사용).
 */
export interface OnchainDelegation {
  delegate: Address;
  delegator: Address;
  authority: Hex;
  caveats: { enforcer: Address; terms: Hex; args: Hex }[];
  salt: bigint;
  signature: Hex;
}

export function toOnchainDelegation(signed: SignedDelegation): OnchainDelegation {
  return {
    delegate: signed.delegate,
    delegator: signed.delegator,
    authority: signed.authority,
    caveats: signed.caveats.map((c) => ({ enforcer: c.enforcer, terms: c.terms, args: "0x" as Hex })),
    salt: signed.salt,
    signature: signed.signature,
  };
}

// ── 실행 인코딩 (ERC-7579 single execution) ──────────────────────────────
// executionCallData = packed(target(20B), value(32B), callData) — ExecutionLib.encodeSingle 정본.
export function encodeSingleExecution(target: Address, value: bigint, callData: Hex): Hex {
  return encodePacked(["address", "uint256", "bytes"], [target, value, callData]);
}

// ModeCode: CALLTYPE_SINGLE(0x00) + EXECTYPE_DEFAULT(0x00) + unused(4B 0) + MODE_DEFAULT(4B 0)
// + payload(22B 0) = 32 zero bytes. ModeLib.encodeSimpleSingle()의 정본 결과와 동일 (all-zero bytes32).
export const MODE_CODE_SIMPLE_SINGLE: Hex = ("0x" + "00".repeat(32)) as Hex;
