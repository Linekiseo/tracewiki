from checkout.pricing import CheckoutService, calculate_total
from payments.gateway import PaymentGateway


def test_gold_customer_discount() -> None:
    assert calculate_total(100.0, "US", "gold") == 96.3


def test_rejects_unknown_currency() -> None:
    service = CheckoutService(PaymentGateway())
    try:
        service.submit_order(10.0, "US", "BTC")
    except ValueError as exc:
        assert "unsupported currency" in str(exc)
    else:
        raise AssertionError("expected unsupported currency")


def test_total_with_tax() -> None:
    assert calculate_total(100.0, "EU") == 120.0
