# Payment architecture

The order service creates the order and forwards the payment request to the payment service.
Coupons are optional. An order without a coupon must still be accepted and must store a null
coupon identifier.

The `create_order` function is the entry point for order creation. Failures in that function can
prevent checkout but do not affect product browsing.

