"""Fail-closed ERC-20 execution simulation against a local Anvil node.

The simulator resolves the *exact* transaction fields -- ``from``, ``to``,
``value``, ``data``, ``nonce`` and ``gas`` -- before taking a snapshot, then
sends those exact fields inside the snapshot.  That is what lets the candidate
built from the resulting evidence claim to describe the same transaction that
was simulated, rather than a similar one.

It always reverts the snapshot, proves balance and nonce restoration, and only
then hands evidence to a caller-supplied decision callback.  The callback
decides; it must not broadcast.  Broadcasting is the caller's job after
``simulate_transfer`` has returned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ERC20_TRANSFER_SELECTOR = "a9059cbb"
ERC20_BALANCE_OF_SELECTOR = "70a08231"
ZERO_ADDRESS = "0x" + "0" * 40
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class RpcSimulationError(RuntimeError):
    """Raised when the simulation cannot establish safe, complete evidence."""


class JsonRpcTransport(Protocol):
    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _normalise_address(value: str, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        raise RpcSimulationError(f"{field} must be a 20-byte 0x-prefixed address")
    try:
        int(value[2:], 16)
    except ValueError as exc:
        raise RpcSimulationError(f"{field} is not hexadecimal") from exc
    return value.lower()


def _quantity(value: int, *, field: str) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RpcSimulationError(f"{field} must be a non-negative integer")
    return hex(value)


def _parse_quantity(value: Any, *, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RpcSimulationError(f"{field} must be an RPC hex quantity")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise RpcSimulationError(f"{field} is not a valid RPC hex quantity") from exc


#: Public name for the RPC quantity parser; other Core modules read the same node.
parse_rpc_quantity = _parse_quantity


def encode_erc20_transfer(recipient: str, amount: int) -> str:
    """Return calldata for ``transfer(address,uint256)`` without ABI packages."""
    recipient = _normalise_address(recipient, field="recipient")
    _quantity(amount, field="amount")
    return "0x" + ERC20_TRANSFER_SELECTOR + recipient[2:].rjust(64, "0") + f"{amount:064x}"


def encode_erc20_balance_of(account: str) -> str:
    """Return calldata for ``balanceOf(address)`` without ABI packages."""
    account = _normalise_address(account, field="account")
    return "0x" + ERC20_BALANCE_OF_SELECTOR + account[2:].rjust(64, "0")


def decode_erc20_uint256(result: Any) -> int:
    """Decode a single ABI uint256 return value, rejecting malformed values."""
    if not isinstance(result, str) or not result.startswith("0x") or len(result) != 66:
        raise RpcSimulationError("ERC-20 call did not return one 32-byte word")
    try:
        return int(result[2:], 16)
    except ValueError as exc:
        raise RpcSimulationError("ERC-20 return value is not hexadecimal") from exc


class JsonRpcClient:
    """Small stdlib-only JSON-RPC 2.0 client with injectable transport for tests."""

    def __init__(self, endpoint: str | None = None, *, transport: JsonRpcTransport | None = None) -> None:
        if transport is None and (not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://"))):
            raise ValueError("endpoint must be an http(s) URL when transport is not supplied")
        self._endpoint = endpoint
        self._transport = transport
        self._next_id = 1

    @property
    def is_local_controlled_endpoint(self) -> bool:
        """Whether the client targets a loopback Anvil node (or a test transport)."""
        if self._transport is not None:
            return True
        assert self._endpoint is not None
        return urlparse(self._endpoint).hostname in LOOPBACK_HOSTS

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": [] if params is None else params,
        }
        try:
            response = self._transport(payload) if self._transport else self._post(payload)
        except (OSError, URLError, TimeoutError) as exc:
            raise RpcSimulationError(f"JSON-RPC transport failed for {method}") from exc
        if not isinstance(response, Mapping) or response.get("id") != request_id:
            raise RpcSimulationError(f"JSON-RPC response mismatch for {method}")
        if "error" in response:
            error = response["error"]
            raise RpcSimulationError(f"JSON-RPC {method} failed: {error!r}")
        if "result" not in response:
            raise RpcSimulationError(f"JSON-RPC {method} omitted result")
        return response["result"]

    def _post(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        assert self._endpoint is not None
        request = Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=15) as response:  # nosec B310 - loopback-only, enforced by the simulator
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise RpcSimulationError("JSON-RPC response was not an object")
        return decoded


@dataclass(frozen=True)
class SimulationContext:
    client_version: str
    chain_id: int
    block_number: int
    block_hash: str
    sender_nonce: int


@dataclass(frozen=True)
class ERC20TransferRequest:
    token: str
    sender: str
    recipient: str
    amount: int
    gas_limit: int | None = None

    def validated(self) -> "ERC20TransferRequest":
        if self.gas_limit is not None:
            _quantity(self.gas_limit, field="gas_limit")
            if self.gas_limit == 0:
                raise RpcSimulationError("gas_limit must be positive when supplied")
        return ERC20TransferRequest(
            token=_normalise_address(self.token, field="token"),
            sender=_normalise_address(self.sender, field="sender"),
            recipient=_normalise_address(self.recipient, field="recipient"),
            amount=_parse_quantity(_quantity(self.amount, field="amount"), field="amount"),
            gas_limit=self.gas_limit,
        )


@dataclass(frozen=True)
class SimulationEvidence:
    """What the node actually did, plus proof it was undone.

    ``transaction`` holds the exact field set that was submitted, including the
    ``nonce`` and ``gas`` resolved before the snapshot.  ``gas_used`` is what the
    receipt reported for that submission.
    """

    context: SimulationContext
    request: ERC20TransferRequest
    transaction: Mapping[str, str]
    transaction_hash: str
    receipt: Mapping[str, Any]
    gas_limit: int
    gas_used: int
    sender_balance_before: int
    sender_balance_after: int
    recipient_balance_before: int
    recipient_balance_after: int
    reverted: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationResult:
    evidence: SimulationEvidence
    gate_accepted: bool


SimulationGate = Callable[[SimulationEvidence], bool]


class ControlledErc20Simulator:
    """Run one exact ERC-20 transfer on Anvil, revert it, then ask for a decision."""

    def __init__(self, rpc: JsonRpcClient, *, receipt_timeout_seconds: float = 10.0, poll_interval_seconds: float = 0.05) -> None:
        if receipt_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("receipt timeout and poll interval must be positive")
        self._rpc = rpc
        self._receipt_timeout_seconds = receipt_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def simulate_transfer(self, request: ERC20TransferRequest, gate: SimulationGate | None) -> SimulationResult:
        """Return evidence only if Anvil execution and state restoration both succeed.

        A missing decision callback is treated as a rejected execution.  The
        callback runs after ``evm_revert`` is confirmed, so it cannot mistake a
        simulated state change for permission to broadcast it, and it is not the
        sender: broadcasting happens outside this method.
        """
        if gate is None:
            raise RpcSimulationError("an explicit gate is required; refusing to simulate without one")
        request = request.validated()
        if request.token == ZERO_ADDRESS or request.sender == ZERO_ADDRESS or request.recipient == ZERO_ADDRESS:
            raise RpcSimulationError("zero address is not accepted for controlled simulation")
        if request.sender == request.recipient:
            raise RpcSimulationError("self-transfer cannot establish balance-floor evidence")
        if not self._rpc.is_local_controlled_endpoint:
            raise RpcSimulationError("endpoint is not loopback; refusing controlled eth_sendTransaction")

        client_version = self._rpc.call("web3_clientVersion")
        if not isinstance(client_version, str) or "anvil" not in client_version.lower():
            raise RpcSimulationError("endpoint is not Anvil; refusing eth_sendTransaction")
        chain_id = _parse_quantity(self._rpc.call("eth_chainId"), field="eth_chainId")

        # Resolve every transaction field before the snapshot so the simulated
        # submission and the later real submission are the same request.
        context = self._capture_context(client_version, chain_id, request.sender)
        transaction = {
            "from": request.sender,
            "to": request.token,
            "value": "0x0",
            "data": encode_erc20_transfer(request.recipient, request.amount),
            "nonce": hex(context.sender_nonce),
        }
        gas_limit = self._resolve_gas_limit(request, transaction)
        transaction["gas"] = hex(gas_limit)

        snapshot = self._rpc.call("evm_snapshot")
        if not isinstance(snapshot, str) or not snapshot:
            raise RpcSimulationError("Anvil did not return a snapshot id")

        original_error: Exception | None = None
        evidence: SimulationEvidence | None = None
        try:
            sender_before = self._balance_of(request.token, request.sender)
            recipient_before = self._balance_of(request.token, request.recipient)
            transaction_hash = self._rpc.call("eth_sendTransaction", [dict(transaction)])
            if not isinstance(transaction_hash, str) or not transaction_hash.startswith("0x"):
                raise RpcSimulationError("eth_sendTransaction did not return a transaction hash")
            receipt = self._wait_for_successful_receipt(transaction_hash)
            gas_used = _parse_quantity(receipt.get("gasUsed"), field="receipt.gasUsed")
            if gas_used == 0 or gas_used > gas_limit:
                raise RpcSimulationError("receipt gasUsed is not within the submitted gas limit")
            sender_after = self._balance_of(request.token, request.sender)
            recipient_after = self._balance_of(request.token, request.recipient)
            self._verify_balance_delta(request, sender_before, sender_after, recipient_before, recipient_after)
            evidence = SimulationEvidence(
                context=context,
                request=request,
                transaction=dict(transaction),
                transaction_hash=transaction_hash,
                receipt=receipt,
                gas_limit=gas_limit,
                gas_used=gas_used,
                sender_balance_before=sender_before,
                sender_balance_after=sender_after,
                recipient_balance_before=recipient_before,
                recipient_balance_after=recipient_after,
                reverted=False,
            )
        except Exception as exc:
            original_error = exc
        finally:
            reverted = self._rpc.call("evm_revert", [snapshot])
            if reverted is not True:
                revert_error = RpcSimulationError("evm_revert was not confirmed true")
                if original_error is not None:
                    raise revert_error from original_error
                raise revert_error
            if evidence is not None:
                self._verify_restored_state(evidence)
                evidence = replace(evidence, reverted=True)

        if original_error is not None:
            raise original_error
        assert evidence is not None
        try:
            accepted = gate(evidence)
        except Exception as exc:
            raise RpcSimulationError("execution gate raised an exception; failing closed") from exc
        return SimulationResult(evidence=evidence, gate_accepted=accepted is True)

    def _resolve_gas_limit(self, request: ERC20TransferRequest, transaction: Mapping[str, str]) -> int:
        """Use the caller's explicit limit, or estimate one before the snapshot."""
        if request.gas_limit is not None:
            return request.gas_limit
        estimate_payload = {key: value for key, value in transaction.items() if key != "nonce"}
        estimated = _parse_quantity(self._rpc.call("eth_estimateGas", [estimate_payload]), field="eth_estimateGas")
        if estimated <= 0:
            raise RpcSimulationError("eth_estimateGas returned a non-positive gas limit")
        return estimated

    def _capture_context(self, client_version: str, chain_id: int, sender: str) -> SimulationContext:
        block = self._rpc.call("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, Mapping):
            raise RpcSimulationError("eth_getBlockByNumber did not return a block object")
        number = _parse_quantity(block.get("number"), field="block.number")
        block_hash = block.get("hash")
        if not isinstance(block_hash, str) or len(block_hash) != 66 or not block_hash.startswith("0x"):
            raise RpcSimulationError("block.hash is missing or malformed")
        nonce = _parse_quantity(self._rpc.call("eth_getTransactionCount", [sender, "latest"]), field="sender nonce")
        return SimulationContext(client_version, chain_id, number, block_hash.lower(), nonce)

    def _balance_of(self, token: str, account: str) -> int:
        result = self._rpc.call("eth_call", [{"to": token, "data": encode_erc20_balance_of(account)}, "latest"])
        return decode_erc20_uint256(result)

    def _wait_for_successful_receipt(self, transaction_hash: str) -> Mapping[str, Any]:
        deadline = time.monotonic() + self._receipt_timeout_seconds
        while time.monotonic() < deadline:
            receipt = self._rpc.call("eth_getTransactionReceipt", [transaction_hash])
            if receipt is not None:
                if not isinstance(receipt, Mapping):
                    raise RpcSimulationError("eth_getTransactionReceipt returned malformed receipt")
                if _parse_quantity(receipt.get("status"), field="receipt.status") != 1:
                    raise RpcSimulationError("ERC-20 transaction reverted; simulation failed closed")
                if receipt.get("transactionHash") != transaction_hash:
                    raise RpcSimulationError("receipt transaction hash does not match submitted transaction")
                return dict(receipt)
            time.sleep(self._poll_interval_seconds)
        raise RpcSimulationError("timed out waiting for Anvil transaction receipt")

    @staticmethod
    def _verify_balance_delta(request: ERC20TransferRequest, sender_before: int, sender_after: int, recipient_before: int, recipient_after: int) -> None:
        if sender_before - sender_after != request.amount:
            raise RpcSimulationError("sender ERC-20 balance delta does not match requested amount")
        if recipient_after - recipient_before != request.amount:
            raise RpcSimulationError("recipient ERC-20 balance delta does not match requested amount")

    def _verify_restored_state(self, evidence: SimulationEvidence) -> None:
        sender = self._balance_of(evidence.request.token, evidence.request.sender)
        recipient = self._balance_of(evidence.request.token, evidence.request.recipient)
        nonce = _parse_quantity(self._rpc.call("eth_getTransactionCount", [evidence.request.sender, "latest"]), field="restored sender nonce")
        if sender != evidence.sender_balance_before or recipient != evidence.recipient_balance_before or nonce != evidence.context.sender_nonce:
            raise RpcSimulationError("evm_revert returned true but state was not restored")
