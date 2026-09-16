"""Administrative payment operations with intentionally same-named symbols."""


class PaymentGateway:
    def charge(self, account_id: str, cents: int) -> str:
        return f"manual:{account_id}:{cents}"


def submit_order(order_id: str) -> str:
    return f"admin:{order_id}"
