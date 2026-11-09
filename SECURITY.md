# Security Policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting ("Security" tab, "Report a vulnerability") on this repository.

We aim to acknowledge reports within 3 working days and to ship a fix or mitigation for confirmed issues as quickly as severity warrants.

## Scope

The application code in this repository and its default deployment configuration. Third-party services and self-hosted instances run by others are out of scope.

## Threat model

The system's assets, trust boundaries, mitigations and known residual risks are documented in [docs/security/threat-model.md](docs/security/threat-model.md). Security notes for running an instance are in the [self-hosting guide](docs/runbooks/self-hosting-compose.md#security-notes).

## Supported versions

Pre-1.0: only the latest `main` is supported.
