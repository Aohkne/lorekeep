# Payments Service

The payments-api is a Go service owned by team-backend.

It depends on the auth service for token validation.

On 2024-01-15 we launched payments-api v1.

As of 2025-03-01 the auth dependency was removed in favor of internal signing.

Decision ADR-007: payments-api adopts internal signing, decided by team-backend.
