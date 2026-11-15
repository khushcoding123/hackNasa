"""User-supplied relay deployment estimates in USD; no market pricing is implied."""
from decimal import Decimal, ROUND_HALF_UP


DEMO_ITEMS = (
    ("Hardware", "Structure and mast", 20000),
    ("Communications", "Radios and electronics", 20000),
    ("Communications", "Antennas", 10000),
    ("Power", "Batteries", 15000),
    ("Power", "Solar equipment", 10000),
    ("Thermal", "Thermal protection", 10000),
    ("Installation", "Deployment, integration and installation", 15000),
)
DEMO_RELAY_COST = sum(price for _, _, price in DEMO_ITEMS)
SCOPE = ("Includes installed network nodes, communications equipment, integration, deployment and installation. "
         "Excludes the lander, launch, satellite services, "
         "ongoing operations and broader mission costs.")


def unit_cost(relay_cost_usd):
    value = DEMO_RELAY_COST if relay_cost_usd is None else relay_cost_usd
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def relay_limit(max_relays, budget_usd, relay_cost_usd, fixed_nodes=0):
    price = unit_cost(relay_cost_usd)
    if budget_usd is None or price == 0:
        return max_relays
    available = max(Decimal(0), Decimal(str(budget_usd)) - price * fixed_nodes)
    affordable = int(available // price)
    return min(max_relays, affordable)


def cost_report(count, budget_usd, relay_cost_usd):
    demo = relay_cost_usd is None
    price = unit_cost(relay_cost_usd)
    items = DEMO_ITEMS if demo else (("Installed network node", "All-in installed network node (user estimate)", price),)
    lines = [dict(category=category, item=item, quantity=count, unit_cost_usd=float(value),
                  total_usd=float(Decimal(str(value)) * count)) for category, item, value in items]
    total = sum((Decimal(str(line["total_usd"])) for line in lines), Decimal(0))
    remaining = None if budget_usd is None else Decimal(str(budget_usd)) - total
    return dict(currency="USD", budget_usd=budget_usd, relay_cost_usd=float(price),
                items=lines, pricing_basis="illustrative" if demo else "user",
                total_usd=float(total), remaining_usd=None if remaining is None else float(remaining),
                within_budget=remaining is None or remaining >= 0,
                assumptions=("Illustrative demo prices, not supplier quotes. " if demo else "User-supplied all-in installed-relay estimate. ") + SCOPE)
