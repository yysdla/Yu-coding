"""Order creation and optional coupon handling."""


class Coupon:
    def __init__(self, coupon_id: str) -> None:
        self.id = coupon_id


class OrderRequest:
    def __init__(self, coupon: Coupon | None) -> None:
        self.coupon = coupon


def create_order(request: OrderRequest) -> dict[str, str | None]:
    """Create an order from the incoming request.

    route: POST /orders
    """
    coupon_id = request.coupon.id
    return {"status": "created", "coupon_id": coupon_id}
