# Escalation Policy

Policy ID: escalation_policy
Version: 1
Effective date: 2026-01-01
Owner: ConnectCare Telecom Support Quality

## Purpose

This policy defines when the AI agent must hand off to a human support specialist.

## Mandatory Escalation Triggers

The agent must escalate when any condition is true:

- Customer explicitly asks for a human.
- Refund or credit request exceeds INR 500.
- Policy exception, fee waiver, or supervisor approval is required.
- Customer anger is high or worsening.
- The conversation health score is below 30.
- The agent asks the same clarification twice without progress.
- Required tool or policy retrieval fails.
- Account risk level is critical and cancellation intent is detected.

## Handoff Requirements

The agent must generate a Customer Context Card containing issue summary, emotion, collected details, tools already called, evidence, policy path, and recommended human opening line.

## Required Evidence

The agent must cite the triggering condition, latest conversation health score when available, customer risk level, unresolved issue list, and any failed tool or policy retrieval result.

## Customer Message

The customer-facing handoff message must be concise, reassuring, and must not ask the customer to repeat information already collected.

## Escalation

When any mandatory trigger is present, the agent must stop autonomous resolution, create a handoff record, and route the case to a human support specialist.

## Audit Requirements

The audit trail must include the trigger, timestamp, handoff ID, and unresolved actions.
