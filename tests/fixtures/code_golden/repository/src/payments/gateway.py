"""Public payment gateway."""

from typing import overload


def normalize_amount(amount: float) -> int:
    return round(amount * 100)


class PaymentGateway:
    @overload
    def charge(self, amount: int, currency: str) -> str: ...

    @overload
    def charge(self, amount: float, currency: str) -> str: ...

    def charge(self, amount: int | float, currency: str) -> str:
        cents = amount if isinstance(amount, int) else normalize_amount(amount)
        return f"payment:{currency}:{cents}"
