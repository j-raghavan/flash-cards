# ADR 0014: Daily goal, streak, study statistics, replan diff and undo

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The requirements:

- F-PROG-1 (P1): a daily goal (cards per day), a streak, and a completion celebration.
- F-PROG-3 (P1): a review heatmap, a retention curve and a forecast of upcoming reviews.
- F-PLAN-3 (P1): the plan recomputes after missed days and after card additions, and the user sees what changed.
- F-STUDY-3 (P0): undo the last answer, and edit a card during a session.

Before this change the plan was already recomputed from live card state on every read (so it adapted by itself), and notes could be edited with `PATCH /notes/{note_id}`. Nothing recorded a day boundary, a goal, an earlier plan, or the schedule a card had before an answer.

The server does not know where a user is, so "a day" has no meaning until the user says which time zone they live in. Review logs grow without bound, so statistics must not load them row by row.

## Decision

### Study settings and the day boundary

- Each user has study settings: a daily goal (1 to 1000 cards, default 20) and an IANA time zone (default `UTC`), read and changed with `GET` and `PATCH /progress/settings`.
- A time zone is accepted only if it is one of `zoneinfo.available_timezones()`; anything else answers 422.
- A day runs from local midnight to local midnight in that zone, computed with `zoneinfo`, so daylight saving changes are handled (a day can be 23 or 25 hours long).
- Settings are included in the account export and removed with the account.

### Aggregation in storage, day mapping in the domain

- Storage never needs the zone rules. A new read port, `StudyStatsRepository`, returns review counts (and how many were recalled) and due counts grouped into epoch-aligned 15-minute buckets, aggregated in the database (`GROUP BY` over `floor(epoch / 900)`).
- The domain maps buckets to local days. Every time zone in use today has an offset that is a whole number of quarter hours, so a bucket never straddles a local midnight, and the in-memory and SQL adapters agree by construction.
- The response size is bounded by the time range, not by the number of reviews: at most 96 buckets per day of range, and in practice only the quarter hours in which the user studied.
- A composite index on `review_logs (owner_id, reviewed_at)` serves the range reads and the most-recent-review lookup.

### Daily goal and streak

- `GET /progress/today` returns today's local date, the reviews done today, the goal, whether it is met, and the streak.
- The streak is the number of consecutive local days meeting the current goal, ending today if today is already met, otherwise ending yesterday: an unfinished today never breaks a streak. It is derived from review logs at read time, so changing the goal or the zone re-reads history consistently and there is no counter to drift. It counts back at most 366 days.
- Every answer (`POST /cards/{card_id}/review`) returns `daily_goal` with `just_completed`, true exactly when this answer brought today's count to the goal: the signal for the client to celebrate. The answer path skips the streak, so it reads only today's buckets.

### Heatmap, retention curve and forecast

- `GET /progress/stats` returns all three, overall or for one deck (`deck_id`, ownership checked).
- Heatmap: reviews per local day for the last `days` days, today included (1 to 366, default 90), zero-filled.
- Retention curve: the actual recall rate per local week (Monday start) over the same range, read from the Monday of the first week so every point covers a whole week: the share of reviews rated Hard, Good or Easy. Weeks without reviews are omitted. Review logs do not record the card's state at review time, so a new card's first Again counts as a lapse; the curve is a measure of recall, not of FSRS retrievability.
- Forecast: the number of scheduled, unsuspended cards coming due on each of the next `forecast_days` local days (1 to 90, default 30). Overdue cards count today. New cards are not reviews and are not counted.

### Replan diff

- The plan the user last accepted is kept per goal as a baseline (`plan_baselines`, removed with its goal): the plan's numbers plus the exam date and target retention it came from.
- `GET /goals/{goal_id}/plan` recomputes the plan and adds `changes`, comparing it with the baseline: the plan before, the daily load (new plus review cards per day) before and after, and the reasons. A goal's first plan becomes its baseline, so its `changes` is null; the insert is idempotent (`ON CONFLICT DO NOTHING`), so concurrent first reads agree.
- `POST /goals/{goal_id}/plan/accept` makes the current plan the new baseline.
- Reasons, each with a count where one applies:
  - `cards_added` and `cards_removed`: the net change in the goal's unsuspended cards.
  - `cards_started`: the net growth of the reviewed set (new cards studied for the first time).
  - `missed_days`: local days after the baseline's day and before today with no reviews in the goal's decks (at most 366).
  - `days_passed`: fewer days left until an unchanged exam date.
  - `exam_date_changed` and `target_changed`: the goal itself was edited.

### Undo the last answer

- Each review log now keeps the schedule the card had just before the answer (or a marker that the card was new). Reviews recorded before this change, or imported, have no snapshot and cannot be undone.
- Answering returns a `review_id`; `POST /reviews/{review_id}/undo` takes that answer back: the log is deleted and the previous schedule restored (or removed, making the card new again). A retried answer (same idempotency key) returns the original `review_id`.
- "Most recent" is a per-owner last-answer record (`review_last_answers`) that every new answer replaces, not the newest surviving log. Deleting the card of the latest answer therefore never promotes an older answer.
- Only that answer can be undone, only within 10 minutes of answering, and only while the card's schedule is still the one that answer produced (a reset or import since then refuses). Refusals answer 409 with the reason; someone else's or an unknown review answers 404.
- Undo is a single step: once undone, the record is flagged and nothing older can be undone until the next answer.
- Idempotent: repeating the same undo answers 404 and changes nothing. The review id is explicit, so a retried request can never take back a different answer.
- Deleting the log frees its idempotency key, so the flagged record keeps it reserved: a late retry of the undone answer (same card and key) answers 409 instead of recording it again.
- The undo response carries today's goal progress, so the client's counter stays correct. The last-answer record is deleted with the owner's review logs.
- Concurrency: answering and undoing take a per-owner transaction-scoped advisory lock (`pg_advisory_xact_lock`) first, so an undo never interleaves with a new answer of the same user. The log is removed with `DELETE ... RETURNING`, so only one of two racing undos restores anything.

### Editing a card in a session

- `PATCH /notes/{note_id}` already keeps a card's id, schedule and history when its ordinal survives the edit (ADR 0011), so a session continues with the edited card and an undo of its last answer still applies. A cloze deletion removed by the edit takes its card and review logs with it, after which there is nothing to undo.

## Consequences

- A migration adds the undo snapshot columns and the index to `review_logs`, and the `review_last_answers`, `study_settings` and `plan_baselines` tables.
- Every answer does two more small reads (settings and today's buckets) and one more lock; all are indexed or keyed.
- Statistics stay correct if the scheduler or the zone changes, because they are recomputed from logs and schedules at read time.
- A time zone with an offset that is not a multiple of 15 minutes (none exist today) would place some reviews on the neighbouring day.
- The streak uses the current goal for every past day; raising the goal can shorten an existing streak.
