"""Restricted fraud rules used only for ACL refusal cases."""


def score_fraud_risk(account_id: str, amount: float) -> float:
    return 0.95 if account_id.startswith("blocked-") and amount > 500 else 0.05
