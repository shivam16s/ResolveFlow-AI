# Service Credit Policy

Policy ID: service_credit_policy
Version: 1
Effective date: 2026-01-01
Owner: ConnectCare Telecom Customer Operations

## Purpose

This policy defines when an automatic service credit may be applied for verified broadband service disruption.

## Eligibility

A customer is eligible for a service credit when all conditions are true:

- The outage is verified in the ConnectCare outage system.
- The outage affected the customer's registered service location.
- The verified disruption lasted at least 6 continuous hours.
- The customer has not already received a service credit for the same outage event.
- The customer's account is active or pending cancellation, not suspended for non-payment.

## Credit Amount

- Verified outage from 6 to 12 hours: INR 300 credit.
- Verified outage longer than 12 hours: INR 500 credit.
- Verified outage shorter than 6 hours: no automatic credit; create a service ticket if symptoms continue.

## Required Evidence

The agent must cite the outage record, customer location, outage duration, and account status before applying credit.

## Allowed Actions

The agent may call `apply_credit` only after policy validation passes. The reason must include the outage ID and duration.

## Escalation

Escalate when the customer requests more than INR 500, the outage is unverified, the account is suspended, or the same event already has a credit.
