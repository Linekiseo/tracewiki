"""Dirty-worktree loyalty discount rules."""


def loyalty_discount(subtotal: float, customer_tier: str) -> float:
    """Return the loyalty rebate for the current customer tier."""
    rates = {"standard": 0.0, "silver": 0.05, "gold": 0.10}
    return subtotal * rates.get(customer_tier, 0.0)
