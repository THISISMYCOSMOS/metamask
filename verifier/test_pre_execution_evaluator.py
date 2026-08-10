from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from evaluate_pre_execution import evaluate_pre_execution
from pre_execution_models import PreExecutionCandidate
from synthesis_models import PolicyApproval


ROOT = Path(__file__).resolve().parent.parent


class PreExecutionEvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.approval = PolicyApproval.model_validate(
            json.loads((ROOT / "specs" / "mvp-user-policy-approval.json").read_text(encoding="utf-8"))
        )
        cls.rejected_candidate = json.loads(
            (ROOT / "traces" / "mvp-candidate-reject.json").read_text(encoding="utf-8")
        )

    def envelope(self, portfolio: dict | None = None) -> dict:
        candidate = copy.deepcopy(portfolio or self.rejected_candidate)
        return {
            "schemaVersion": 1,
            "kind": "pre-execution-candidate",
            "candidateId": "g3-step-020",
            "historySha256": candidate["sourceTraceHashedContentSha256"],
            "context": {
                "chainId": 1,
                "currentBlockNumber": "25700019",
                "currentBlockHash": "0x" + "ab" * 32,
                "senderNonce": "19",
            },
            "execution": {
                "chainId": 1,
                "fromAddress": "0x" + "11" * 20,
                "toAddress": "0x" + "22" * 20,
                "value": "0",
                "data": "0x1234",
                "gas": "3000000",
                "nonce": "19",
            },
            "portfolioCandidate": candidate,
        }

    def accepted_portfolio(self) -> dict:
        data = copy.deepcopy(self.rejected_candidate)
        timestamp = data["transitions"][-1]["timestamp"]
        data["traceId"] = "inclusive-limit-candidate"
        data["coverageStartTimestamp"] = str(int(timestamp) - 86_400)
        data["initialValue1e18"] = "28978786716100000000000"
        data["transitions"] = [
            {
                "transitionIndex": 0,
                "timestamp": timestamp,
                "beforeValue1e18": "28978786716100000000000",
                "afterValue1e18": "26979251676100000000000",
                "role": "candidate",
            }
        ]
        return data

    def test_rejected_candidate_is_valid_but_not_accepted(self) -> None:
        candidate = PreExecutionCandidate.model_validate(self.envelope())
        decision = evaluate_pre_execution(self.approval, candidate)
        self.assertFalse(decision["accepted"])
        self.assertEqual([False, False], [item["passed"] for item in decision["evaluations"]])

    def test_inclusive_limit_candidate_is_accepted(self) -> None:
        candidate = PreExecutionCandidate.model_validate(self.envelope(self.accepted_portfolio()))
        decision = evaluate_pre_execution(self.approval, candidate)
        self.assertTrue(decision["accepted"])
        self.assertTrue(all(item["passed"] for item in decision["evaluations"]))

    def test_nonce_and_history_links_fail_closed(self) -> None:
        wrong_nonce = self.envelope()
        wrong_nonce["execution"]["nonce"] = "20"
        with self.assertRaises(ValidationError):
            PreExecutionCandidate.model_validate(wrong_nonce)

        wrong_history = self.envelope()
        wrong_history["historySha256"] = "0x" + "00" * 32
        with self.assertRaises(ValidationError):
            PreExecutionCandidate.model_validate(wrong_history)

    def test_decision_is_deterministic(self) -> None:
        candidate = PreExecutionCandidate.model_validate(self.envelope())
        self.assertEqual(
            evaluate_pre_execution(self.approval, candidate),
            evaluate_pre_execution(self.approval, candidate),
        )

    def test_unknown_fields_are_rejected(self) -> None:
        data = self.envelope()
        data["execution"]["privateKey"] = "not-allowed"
        with self.assertRaises(ValidationError):
            PreExecutionCandidate.model_validate(data)


if __name__ == "__main__":
    unittest.main()
