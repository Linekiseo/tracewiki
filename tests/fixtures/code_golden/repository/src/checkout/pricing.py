"""Checkout pricing orchestration."""

from payments.gateway import PaymentGateway

from .discounts import loyalty_discount
from .tax import compute_tax as calculate_tax


def calculate_total(
    subtotal: float,
    region: str,
    customer_tier: str = "standard",
) -> float:
    """Apply a loyalty rebate and regional tax."""
    discount = loyalty_discount(subtotal, customer_tier)
    taxable = subtotal - discount
    return taxable + calculate_tax(taxable, region)


def validate_currency(currency: str) -> bool:
    return currency in {"USD", "EUR", "CNY"}


def build_receipt_formatter(prefix: str):
    """Exercise a nested function without exporting it as a public API."""

    def format_line(label: str, amount: float) -> str:
        return f"{prefix} {label}: {amount:.2f}"

    return format_line


class CheckoutService:
    def __init__(self, gateway: PaymentGateway) -> None:
        self.gateway = gateway

    def submit_order(
        self,
        subtotal: float,
        region: str,
        currency: str,
        customer_tier: str = "standard",
    ) -> str:
        if not validate_currency(currency):
            raise ValueError(f"unsupported currency: {currency}")
        total = calculate_total(subtotal, region, customer_tier)
        return self.gateway.charge(total, currency)


class AuditFormatter:
    def format(self, event: str) -> str:
        return f"checkout:{event}"
