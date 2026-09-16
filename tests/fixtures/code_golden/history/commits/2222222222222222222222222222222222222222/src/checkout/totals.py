def round_total(amount: float) -> int:
    return round(amount)


def calculate_total(subtotal: float, tax_rate: float) -> float:
    return subtotal * (1 + tax_rate)
