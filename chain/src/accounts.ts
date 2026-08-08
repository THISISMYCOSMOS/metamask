// anvil 기본 테스트 계정만 사용한다. 사용자 환경/.env에서 키를 읽지 않는다.
import { createPublicClient, createWalletClient, http, type Address } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { mainnet } from "viem/chains";
import { ANVIL_RPC, ANVIL_DEFAULT_PRIVATE_KEYS, ACCOUNT_INDEX } from "./config.js";

export const accounts = {
  deployer: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[ACCOUNT_INDEX.deployer]),
  owner: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[ACCOUNT_INDEX.owner]),
  delegate: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[ACCOUNT_INDEX.delegate]),
  counterparty: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[ACCOUNT_INDEX.counterparty]),
  disallowedTarget: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[ACCOUNT_INDEX.disallowedTarget]),
} as const;

export const publicClient = createPublicClient({
  chain: mainnet,
  transport: http(ANVIL_RPC),
});

export function walletFor(account: (typeof accounts)[keyof typeof accounts]) {
  return createWalletClient({ account, chain: mainnet, transport: http(ANVIL_RPC) });
}

export const addressOf = {
  deployer: accounts.deployer.address as Address,
  owner: accounts.owner.address as Address,
  delegate: accounts.delegate.address as Address,
  counterparty: accounts.counterparty.address as Address,
  disallowedTarget: accounts.disallowedTarget.address as Address,
} as const;
