# Escalation Policy

## Account

Account compromise goes to a specialist queue named security.
This includes a hacked login, a password reset request, and a customer who cannot log in.
Do not refund these tickets.

## Billing

A duplicate charge or an invoice dispute goes to a specialist queue named billing.
Do not issue an automatic refund for a billing dispute.

## Refund approval

A refund above the automatic limit waits on the refunds queue for a teammate.
The customer is told that no money has moved yet.

## Other

Use the support queue when the order cannot be found or the sender does not own the order.
Use the support queue when a cancellation arrives after shipment.
Use the shipping queue when a claim is too early to refund.
