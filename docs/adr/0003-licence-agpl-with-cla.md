# ADR 0003: Licence: AGPL-3.0 with a CLA

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

The project starts free and invite-only, with the option of commercial offerings if it succeeds. The first draft used Apache-2.0. Apache-2.0 would let anyone, including competitors, host and sell the software, and a published licence cannot be taken back for the versions already released.

While the maintainer is the sole copyright holder and nothing has been published, changing the licence costs nothing. Moving from a strict licence to a looser one later is easy. Moving the other way is not.

## Decision

- License the code under **AGPL-3.0-only**.
- Require contributors to agree to the **CLA** (`CLA.md`), which lets the maintainer offer commercial licences or relicense later.
- Add SPDX headers (`SPDX-License-Identifier: AGPL-3.0-only`) to source files.
- Keep the option to relax to Apache-2.0 or MIT later if adoption matters more than exclusivity.

## Consequences

- Anyone who runs a modified version as a network service must offer its source to its users, which closes the hosted-service loophole.
- Some companies avoid AGPL software, which may reduce adoption.
- The CLA adds friction for contributors; an automated check keeps it light.
- Self-hosters and individuals are unaffected for ordinary use.
- The CLA template and the licence choice have not had legal review.
