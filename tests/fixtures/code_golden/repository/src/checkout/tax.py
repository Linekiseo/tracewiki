"""Regional tax policies."""


class TaxPolicy:
    RATES = {"US": 0.07, "EU": 0.20, "CN": 0.13}

    def rate_for(self, region: str) -> float:
        return self.RATES.get(region, 0.0)


def compute_tax(amount: float, region: str) -> float:
    policy = TaxPolicy()
    return amount * policy.rate_for(region)
