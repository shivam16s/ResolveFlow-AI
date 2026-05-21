# Duplicate Charge Policy

Policy ID: duplicate_charge_policy
Version: 1
Effective date: 2026-01-01
Owner: ConnectCare Telecom Billing Operations

## Purpose

This policy defines how agents must handle possible duplicate charges.

## Duplicate Charge Definition

A duplicate charge is confirmed when the customer has two or more successful payment records with:

- The same customer ID.
- The same payment amount.
- The same billing date or a time difference under 10 minutes.
- The same payment method or gateway reference family.
- Only one matching invoice for the billing period.

## Agent Procedure

The agent must call `get_invoice_history` and `check_duplicate_charge` before making any adjustment. The agent must not rely only on the customer's statement.

## Allowed Resolution

When duplicate payment is confirmed, the agent may:

- Mark the invoice as disputed if it is not already disputed.
- Apply an account credit up to the duplicate amount when allowed by the policy validator.
- Create a billing ticket for refund review.

## Required Evidence

The audit trail must include invoice ID, both payment IDs, payment amount, payment timestamps, and duplicate detection result.

## Escalation

Escalate if payment records disagree, the customer asks for immediate bank refund, or the duplicate amount exceeds INR 500.
