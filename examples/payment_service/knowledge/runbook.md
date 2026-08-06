# Order service error runbook

When checkout returns HTTP 500, first inspect the traceback for a source file and line number.
Confirm whether the latest release changed optional request fields. If coupon handling fails,
roll back the release or restore the null guard, then add a no-coupon unit test.

