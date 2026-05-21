# Technician Visit Policy

Policy ID: technician_visit_policy
Version: 1
Effective date: 2026-01-01
Owner: ConnectCare Telecom Field Operations

## Purpose

This policy defines when a technician visit may be scheduled.

## Eligibility

A technician visit may be scheduled when at least one condition is true:

- Router diagnostic status is degraded or offline.
- Signal strength is below 50.
- A verified outage has ended but the customer's connection remains unstable.
- The customer completed guided troubleshooting and the verification tool still reports failure.

## Required Checks

The agent must call `run_router_diagnostic` before offering a technician slot unless a human agent has already confirmed hardware damage.

## Allowed Actions

The agent may offer available technician windows and call `schedule_technician` after the customer chooses a slot. A ticket must be created or linked to the visit.

## Fees

No visit fee applies when diagnostics show network-side or router-side failure. Customer-caused equipment damage requires human review.

## Required Evidence

The agent must cite diagnostic status, signal strength, active outage status, troubleshooting attempts, customer account status, and selected appointment slot before scheduling a technician.

## Escalation

Escalate if diagnostics are unavailable, the customer requests emergency dispatch, the account is suspended, or no appointment slots are available.
