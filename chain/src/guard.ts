// fail-closed 포크 안전 가드. 브리프 §2.3의 5개 검사 전부를 통과해야 한다.
// chainId === 1 검사만으로는 부족하다 — 포크된 anvil도 chainId 1을 보고하기 때문이다.
import type { PublicClient } from "viem";
import { ANVIL_RPC, FORK_BLOCK_HASH, FORK_BLOCK_NUMBER, FORK_CHAIN_ID } from "./config.js";

export class ForkGuardError extends Error {
  constructor(message: string) {
    super(`[fork-guard] ${message}`);
    this.name = "ForkGuardError";
  }
}

/**
 * 모든 트랜잭션 전에 호출한다. 5개 검사 전부 통과해야 리턴한다. 하나라도 실패하면 throw.
 * 1. 엔드포인트 호스트가 127.0.0.1 또는 localhost
 * 2. web3_clientVersion 응답에 "anvil" 포함
 * 3. anvil_nodeInfo가 정상 응답 (실제 메인넷 노드는 이 메서드가 없다)
 * 4. eth_chainId === 1
 * 5. 블록 25700000의 해시가 고정 해시와 일치
 */
export async function assertLocalAnvilFork(client: PublicClient): Promise<void> {
  // 1. 호스트 검사
  const url = new URL(ANVIL_RPC);
  if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
    throw new ForkGuardError(
      `RPC host must be 127.0.0.1 or localhost, got "${url.hostname}". ` +
        `ANVIL_RPC=${ANVIL_RPC}`,
    );
  }

  // 2. web3_clientVersion에 "anvil" 포함
  let clientVersion: string;
  try {
    clientVersion = (await client.request({ method: "web3_clientVersion" })) as string;
  } catch (err) {
    throw new ForkGuardError(`web3_clientVersion call failed: ${String(err)}`);
  }
  if (!clientVersion.toLowerCase().includes("anvil")) {
    throw new ForkGuardError(
      `web3_clientVersion does not contain "anvil": "${clientVersion}"`,
    );
  }

  // 3. anvil_nodeInfo 정상 응답 (메인넷 노드에는 없는 메서드)
  try {
    const nodeInfo = await client.request({ method: "anvil_nodeInfo" as any });
    if (!nodeInfo) {
      throw new ForkGuardError("anvil_nodeInfo returned empty response");
    }
  } catch (err) {
    if (err instanceof ForkGuardError) throw err;
    throw new ForkGuardError(`anvil_nodeInfo call failed (not a real anvil node?): ${String(err)}`);
  }

  // 4. eth_chainId === 1
  const chainId = await client.getChainId();
  if (chainId !== FORK_CHAIN_ID) {
    throw new ForkGuardError(`chainId mismatch: expected ${FORK_CHAIN_ID}, got ${chainId}`);
  }

  // 5. 블록 25700000의 해시가 고정 해시와 일치
  const block = await client.getBlock({ blockNumber: FORK_BLOCK_NUMBER });
  if (block.hash?.toLowerCase() !== FORK_BLOCK_HASH.toLowerCase()) {
    throw new ForkGuardError(
      `block ${FORK_BLOCK_NUMBER} hash mismatch: expected ${FORK_BLOCK_HASH}, got ${block.hash}`,
    );
  }
}
