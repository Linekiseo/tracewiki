from .tax import compute_tax as calculate_tax


def calculate_total(subtotal: float, region: str) -> float:
    return subtotal + calculate_tax(subtotal, region)
