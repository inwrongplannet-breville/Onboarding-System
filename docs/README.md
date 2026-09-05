# Project Documentation

This directory contains the technical documentation for the Employee Management
and Onboarding System.

For installation, local development, deployment commands, and a general project
overview, start with the [main README](../README.md).

## Documentation index

| Document | Purpose |
|---|---|
| [AWS architecture](aws-architecture.md) | Defines the AWS services, deployed resources, Lambda functions, request flows, security boundaries, and operational responsibilities. |
| [API reference](api.md) | Documents authentication, roles, endpoints, request and response formats, validation rules, and status codes. |
| [Database design](database-design.md) | Describes the DynamoDB tables, keys, indexes, item structures, access patterns, and lifecycle invariants. |
| [Application design](design.md) | Explains the broader frontend and backend design decisions, workflows, and implementation boundaries. |
| [Testing guide](phase3-testing.md) | Covers automated and manual testing procedures for the application. |
| [Postman collection](Employee-Onboarding.postman_collection.json) | Provides importable API requests for manually exercising the deployed backend. |

## Suggested reading order

1. Read the [main README](../README.md) to understand and run the project.
2. Read the [application design](design.md) for the system workflows.
3. Use the [AWS architecture](aws-architecture.md) for infrastructure and
   service responsibilities.
4. Refer to the [API reference](api.md) and
   [database design](database-design.md) while implementing or reviewing code.
5. Follow the [testing guide](phase3-testing.md) before validating a change.

## Sources of truth

- [`template.yaml`](../template.yaml) is the source of truth for deployed AWS
  infrastructure and permissions.
- The modules under [`src/`](../src/) are the source of truth for backend
  behavior.
- The files under [`js/`](../js/) are the source of truth for frontend behavior.
- The automated tests under [`tests/`](../tests/) define the validated behavior
  and regression coverage.

Documentation should describe generated resources using logical names,
parameters, and stack output keys. Do not add credentials, tokens, account IDs,
secret values, or environment-specific generated identifiers to these files.
