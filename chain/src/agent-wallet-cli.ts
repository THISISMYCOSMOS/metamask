import { execFile } from "node:child_process";
import { join } from "node:path";
import { promisify } from "node:util";

import { PreExecutionGateError, type ExecutionRequest } from "./pre-execution-gate.js";

const execFileAsync = promisify(execFile);
const ADDRESS_PATTERN = /0x[0-9a-fA-F]{40}/g;
const TRANSACTION_HASH_PATTERN = /0x[0-9a-fA-F]{64}/g;
const UINT_PATTERN = /^(0|[1-9][0-9]*)$/;

export interface AgentWalletCliOutput {
  stdout: string;
  stderr: string;
}

export type AgentWalletCliRunner = (
  command: string,
  args: readonly string[],
  timeoutMilliseconds: number,
) => Promise<AgentWalletCliOutput>;

export interface AgentWalletCliSendResult {
  transactionHash: `0x${string}`;
  walletAddress: `0x${string}`;
}

function uniqueMatches(value: string, pattern: RegExp): string[] {
  return [...new Set((value.match(pattern) ?? []).map((item) => item.toLowerCase()))];
}

function decimalQuantity(value: string, field: string): bigint {
  if (!UINT_PATTERN.test(value)) throw new PreExecutionGateError(`${field} is not a canonical uint`);
  const parsed = BigInt(value);
  if (parsed >= 1n << 256n) throw new PreExecutionGateError(`${field} exceeds uint256`);
  return parsed;
}

function hexQuantity(value: string, field: string): `0x${string}` {
  return `0x${decimalQuantity(value, field).toString(16)}`;
}

function requireAddress(value: string, field: string): `0x${string}` {
  if (!/^0x[0-9a-fA-F]{40}$/.test(value)) throw new PreExecutionGateError(`${field} is not an EVM address`);
  return value.toLowerCase() as `0x${string}`;
}

function requireData(value: string): `0x${string}` {
  if (!/^0x(?:[0-9a-fA-F]{2})*$/.test(value)) throw new PreExecutionGateError("execution data is not canonical hex bytes");
  return value.toLowerCase() as `0x${string}`;
}

async function defaultRunner(
  command: string,
  args: readonly string[],
  timeoutMilliseconds: number,
): Promise<AgentWalletCliOutput> {
  try {
    const result = await execFileAsync(command, [...args], {
      encoding: "utf8",
      timeout: timeoutMilliseconds,
      windowsHide: true,
      maxBuffer: 1024 * 1024,
    });
    return { stdout: result.stdout, stderr: result.stderr };
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") throw new PreExecutionGateError("MetaMask Agent Wallet CLI is not installed");
    if (code === "ETIMEDOUT") throw new PreExecutionGateError("MetaMask Agent Wallet CLI timed out");
    throw new PreExecutionGateError("MetaMask Agent Wallet CLI refused the transaction");
  }
}

export function buildAgentWalletTransactionPayload(execution: Readonly<ExecutionRequest>): Record<string, string> {
  if (!Number.isSafeInteger(execution.chainId) || execution.chainId < 1) {
    throw new PreExecutionGateError("execution chainId is invalid");
  }
  return {
    to: requireAddress(execution.toAddress, "execution target"),
    value: hexQuantity(execution.value, "execution value"),
    data: requireData(execution.data),
    gas: hexQuantity(execution.gas, "execution gas"),
    nonce: hexQuantity(execution.nonce, "execution nonce"),
  };
}

export function createAgentWalletCliSender(options: {
  executable?: string;
  intent?: string;
  timeoutMilliseconds?: number;
  runner?: AgentWalletCliRunner;
} = {}): (execution: Readonly<ExecutionRequest>) => Promise<AgentWalletCliSendResult> {
  const windowsNpmExecutable = process.env.APPDATA ? join(process.env.APPDATA, "npm", "mm.cmd") : "mm.cmd";
  const executable = options.executable ?? process.env.MM_CLI_PATH ?? (process.platform === "win32" ? windowsNpmExecutable : "mm");
  const timeoutMilliseconds = options.timeoutMilliseconds ?? 600_000;
  const intent = (options.intent ?? "Execute the exact user-approved delegated ERC-20 transfer").trim();
  const runner = options.runner ?? defaultRunner;
  if (!Number.isSafeInteger(timeoutMilliseconds) || timeoutMilliseconds < 1 || timeoutMilliseconds > 600_000) {
    throw new PreExecutionGateError("Agent Wallet timeout must be between 1 and 600000 milliseconds");
  }
  if (!intent || intent.length > 400) throw new PreExecutionGateError("Agent Wallet intent is invalid");

  return async (execution) => {
    const expectedWallet = requireAddress(execution.fromAddress, "execution sender");
    const addressResult = await runner(executable, ["wallet", "address"], timeoutMilliseconds);
    const activeWallets = uniqueMatches(addressResult.stdout, ADDRESS_PATTERN);
    if (activeWallets.length !== 1 || activeWallets[0] !== expectedWallet) {
      throw new PreExecutionGateError("active MetaMask Agent Wallet does not match the delegated execution sender");
    }

    const payload = buildAgentWalletTransactionPayload(execution);
    const sendResult = await runner(
      executable,
      [
        "wallet",
        "send-transaction",
        "--chain-id",
        String(execution.chainId),
        "--payload",
        JSON.stringify(payload),
        "--wait",
        "--intent",
        intent,
      ],
      timeoutMilliseconds,
    );
    const transactionHashes = uniqueMatches(sendResult.stdout, TRANSACTION_HASH_PATTERN);
    if (transactionHashes.length !== 1) {
      throw new PreExecutionGateError("MetaMask Agent Wallet did not return one confirmed transaction hash");
    }
    return {
      transactionHash: transactionHashes[0] as `0x${string}`,
      walletAddress: expectedWallet,
    };
  };
}
