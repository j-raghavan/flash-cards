# ADR 0027: Members have no monthly page quota by default

- **Status:** Accepted
- **Date:** 2026-11-11
- **Amends:** ADR 0016 (quotas), ADR 0022 (document page limits and admin uploads)

## Context

The monthly AI-page quota defaulted to 10 pages per member (`DEFAULT_MONTHLY_PAGES`, the free tier of PRD R22). A member who uploaded one ordinary document used the month's allowance at once and was refused until the month rolled over. For an instance whose operator is happy to let members generate freely, metered by the instance spend cap alone, the 10-page default was a limit they did not ask for and could only discover after a member hit it.

The per-document page cap (`FLASHCARDS_MAX_UPLOAD_PAGES`, default 300) is a separate control, introduced in ADR 0022 to bound a single job's duration and cost. It is unchanged here.

## Decision

### The default monthly page limit is "no limit"

- A monthly page limit of `None` means unlimited. The instance default (`InstanceQuotas.default_monthly_pages`) and the configuration value (`FLASHCARDS_AI_DEFAULT_MONTHLY_PAGES`) default to `None`, so a fresh instance does not meter members by the page quota.
- An unlimited limit behaves exactly like the admin exemption (ADR 0022): generation is never refused by the page quota, the member's pages are still reserved and counted so the usage report shows them, and the instance monthly spend cap still applies as the only brake.
- The effective limit is resolved as before: a per-user override if set, else the instance default. `None` at either level means unlimited. `GET /account/ai-quota` reports `monthly_pages: null` and `quota_applies: false` for a member with no limit, as it already did for an admin.

### Metering is still available, per instance and per user

- An admin can set a finite instance default at `PUT /admin/quotas`, or a finite per-user limit at `PUT /admin/users/{id}/quota`, to meter some or all members. The free-tier allowance of PRD R22 is now opt-in: set `FLASHCARDS_AI_DEFAULT_MONTHLY_PAGES=10` (or any number) to reinstate it.
- A finite limit is still range-checked to 0..`MAX_MONTHLY_PAGES`. 0 means AI generation is not included; `None` means no limit. A per-user override remains a finite number or "cleared" (inherit the default); there is no per-user "unlimited" override, since an unlimited instance default already leaves every member without an override unlimited.
- The admin screens show an empty page-limit field for "no limit" and a user's limit as "no limit" when the resolved limit is unlimited; the upload and account screens tell a member they have no monthly limit while still showing the per-document cap.

## Consequences

- Out of the box, members can generate as many pages a month as the spend cap allows. Operators who need a free tier set the number explicitly.
- `instance_quotas.default_monthly_pages` becomes nullable (migration 0021). Existing rows keep whatever limit an admin had set; only new instances inherit the unlimited default. A downgrade backfills `NULL` to 10 before making the column `NOT NULL` again.
- The page quota's accounting is unchanged for metered members, and the usage report still shows every member's reserved pages, metered or not.
