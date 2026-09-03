import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { createAgentWalletCliSender } from "./agent-wallet-cli.js";
import {
  executeAgentWalletDirectBundle,
  parseAgentWalletDirectBundle,
  ViemDirectFloorRpc,
} from "./agent-wallet-direct-floor.js";

function parseArguments(args: readonly string[]): { bundlePath: string; broadcast: boolean } {
  let bundlePath = "";
  let broadcast = false;
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--bundle" && index + 1 < args.length) {
      bundlePath = args[index + 1];
      index += 1;
    } else if (arg === "--broadcast") {
      broadcast = true;
    } else {
      throw new Error(`unknown or incomplete argument: ${arg}`);
    }
  }
  if (!bundlePath) throw new Error("--bundle <path> is required");
  return { bundlePath: resolve(bundlePath), broadcast };
}

async function main(): Promise<void> {
  const args = parseArguments(process.argv.slice(2));
  const rpcUrl = process.env.AGENT_WALLET_RPC_URL?.trim();
  if (!rpcUrl) throw new Error("AGENT_WALLET_RPC_URL is required");
  const raw = JSON.parse(await readFile(args.bundlePath, "utf8"));
  const bundle = parseAgentWalletDirectBundle(raw);
  const result = await executeAgentWalletDirectBundle({
    bundle,
    rpc: new ViemDirectFloorRpc(rpcUrl),
    broadcast: args.broadcast,
    send: createAgentWalletCliSender({ intent: "Send the exact approved Sepolia USDC research transfer" }),
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "unknown Agent Wallet direct runtime failure";
  process.stderr.write(`Agent Wallet direct execution failed closed: ${message}\n`);
  process.exitCode = 1;
});
