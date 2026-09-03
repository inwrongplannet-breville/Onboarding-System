# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Employee Management & Onboarding System for Breville HR. Vanilla JS frontend (no build step, no
npm), Python 3.13 Lambdas behind API Gateway, two DynamoDB tables, S3 for documents. Deployed via
AWS SAM to `onboarding-system-dev` (`eu-north-1`).

The three canonical docs are authoritative and this file does not duplicate them — read them for
depth, come back here for orientation:

| Doc | Covers |
|---|---|
| [docs/database-design.md](docs/database-design.md) | The DynamoDB layout in full: item shape, access patterns, invariants, cost, the promotion sequences |
| [docs/api.md](docs/api.md) | Every endpoint, request/response shape, curl examples |
| [docs/design.md](docs/design.md) | Frontend architecture and the reasoning behind past decisions |

## Commands

```bash
# Frontend - serve, don't double-click index.html (file:// origin breaks CORS)
py -m http.server 8000

# Backend tests - no AWS account needed, runs against in-memory DynamoDB (moto)
py -m pip install -r requirements-dev.txt
py -m pytest                              # ~436 tests, ~60s
py -m pytest tests/test_promotion.py -q   # one file
py -m pytest tests/test_promotion.py::test_an_intern_named_as_a_reporting_manager_is_rejected  # one test

# Reset the deployed data - drives the real REST API (including the promote
# sequences), not the tables directly, so a broken seed is a broken API
py scripts/seed_employees.py --wipe --seed --yes

# Deploy (needs AWS SAM CLI + AWS CLI + Python 3.13; Docker not required)
sam validate --lint
sam build
sam deploy                                # samconfig.toml is committed - no arguments needed
py scripts/seed_accounts_secret.py        # first deploy only - populates login accounts in Secrets Manager
sam sync --watch                          # ~5s code pushes while iterating
```

`sam deploy` defaults to `confirm_changeset = true` in `samconfig.toml`; pass
`--no-confirm-changeset` when running it non-interactively.

Tearing down: CloudFormation cannot delete a non-empty S3 bucket, so `sam delete` alone lands in
`DELETE_FAILED`. Empty the `DocumentsBucketName` output bucket first — see README.md's "Deploy the
backend" section for the exact commands.

## Architecture

### Data model — two DynamoDB tables, not three

- **`OnboardingTable`** — people whose onboarding is not finished. PK `employeeKey` = `EMP#<id>`,
  no sort key, checklist embedded as a list attribute on the item (not sibling rows). `entityType`
  is always `"Employee"` here and load-bearing for the Scan filter in `list_employees.py`.
- **`EmployeeTable`** — everyone who has finished onboarding, **employees and interns side by
  side**, told apart only by `entityType` (`"Employee"` / `"Intern"`), never by a separate table or
  key shape. This was briefly two tables (`EmployeeTable` + `InternTable`) and was merged back
  before the first deploy — `entityType` was already the right discriminator, and one shared
  `attribute_not_exists(employeeKey)` makes "promoted as both an employee and an intern"
  structurally impossible in a way two independent tables' conditions never guaranteed. Has a
  `ByReportingManager` GSI (sparse — only intern items carry `reportingManagerId`).

Both tables use the identical key attribute and `EMP#` prefix (`common/keys.py`), so a promoted
record keeps the id it had while onboarding. `common/db.py` exposes `onboarding_table` and
`employee_table`; there is **no single `TABLE_NAME`** on purpose — an unmigrated handler still
importing the old pre-split name fails to import at cold start instead of quietly reading/writing
the wrong table.

**The one thing to get right when touching a handler that reads `EmployeeTable`:** "this id exists"
no longer means "this id is an employee". Every place that validates a reporting manager or a
delete target checks `common/models.is_employee_item()` / `is_intern_item()` explicitly. If you add
a new handler against `EmployeeTable`, ask whether it needs the same check.

`common/repository.py`'s `find_record(id)` tries `OnboardingTable` then `EmployeeTable` and returns
`(api_object, source)` — this is the read every id-addressed endpoint needs once a record can leave
onboarding (`GET /employees/{id}`, the contact PATCH, the document-upload check). Handlers that only
ever touch onboarding (`PUT`, the checklist `PATCH`, archive-`DELETE`) use `load_employee()` instead
and get a 404 for a promoted id — that's deliberate, not a bug to fix.

### Promotion: a sequence of small endpoints, not a transaction

"Move to main employee dashboard" / "Move to intern dashboard" and their reverse (un-promote,
manager reassignment) are each a **short sequence of independently callable endpoints**, driven by
the frontend one call at a time — not a single `TransactWriteItems`, even though this codebase has
held that machinery before (`git show da9ecc3^:src/common/db.py`) and DynamoDB transactions do span
tables. This is a deliberate product choice: every step should be independently triggerable and
observable from the client.

The contract every sequence obeys, and the reason it's safe: **the destructive step is always last,
and every step is idempotent.** An interrupted sequence leaves a harmless duplicate (visible on two
dashboards, or a manager link recorded twice), never a vanished record — re-running the whole
sequence from step 1 always finishes the job. Two server-side guards make this a property of the
API rather than of a well-behaved client: `DELETE /onboarding/{id}` refuses unless the record
already exists in `EmployeeTable`; `DELETE /staff/employees/{id}` and `DELETE /staff/interns/{id}`
refuse unless the onboarding row exists again *and* the record is still inside its 7-day undo
window. See `docs/database-design.md#promotion` for the four full sequences and the known gap (no
automatic compensation for an interrupted sequence — visibility and safe retry are guaranteed,
prevention is not).

`dynamodb:DeleteItem` exists in this stack on exactly three functions (one table each) — the rest of
the system, by deliberate design, has no hard delete anywhere (`DELETE /employees/{id}` archives in
place). If you're adding a new destructive endpoint, that invariant is worth re-reading in
`src/handlers/delete_employee.py`'s docstring before granting `DeleteItem` anywhere new.

### Request handling

Every Lambda handler in `src/handlers/` is wrapped in `@api_handler` (`common/handler.py`), which
catches `BadRequest`/`Forbidden` and turns any other exception into a generic 500 rather than
leaking a stack trace. The convention across handlers: check role (`require_official` /
`require_self` / `require_role`) **before** touching DynamoDB or parsing the body, so a caller who
was never going to be allowed to do this gets a 403 about their account rather than a 400 about
something that was never going to be read.

`common/models.py` is the one place `status`/`progress` are derived from a checklist — never
stored, never recomputed anywhere else, including the frontend. `common/responses.py` is the one
place HTTP responses are shaped (status codes, CORS headers, the error envelope).

### Auth: two roles, enforced server-side only

A client-side route guard (`js/app.js`'s `guard()`) exists purely as a UX convenience — it is not
trusted and is not the security boundary. The actual boundary is a signed JWT (HS256,
`common/tokens.py`, no third-party dependency — `src/requirements.txt` is intentionally empty)
verified by a Lambda authorizer on every route except `POST /login`. An employee's username *is*
their employee number (`common/accounts.py`), which is what lets `require_self` compare the caller
to a record with no separate user-management table. `POST /login` has **zero DynamoDB permission**,
so it cannot check an employee number exists — a typo'd number signs in fine and 404s on first read.

### Frontend: three files, one shared `window.App`

Classic scripts, no bundler, load order load-bearing (`config.js` → `auth.js` → `store.js` →
`ui.js` → `app.js` — see `index.html`). `js/store.js` is the *only* file that calls `fetch()`
against the API (`uploadToS3` is the one deliberate exception, since a presigned POST cannot go
through the same wrapper). `js/ui.js` is pure `state → HTML string`, never touches the DOM or the
store. `js/app.js` is the router and the only place that touches the DOM directly, does state
management, and wires event listeners after every repaint (each render replaces `#app`'s whole
`innerHTML`, so listeners never need explicit teardown).

## Conventions worth knowing before editing

- **Comments explain *why*, not *what*.** Nearly every file in this repo carries prose about the
  trade-off behind a decision, not a restatement of the code. Match that density when editing
  nearby code — a one-line change with no comment reads as an oversight in this codebase.
- **Absent and empty mean the same thing to a reader.** A missing DynamoDB attribute and `""`/`[]`
  are treated identically on the way out (`to_api_employee`, checklist comments, `interns`) —
  don't special-case `None` vs `""` in new code without a reason.
- **Archive, don't delete**, is the rule for anything with history (an employee record). The
  narrow, explicit exceptions are the promotion routes, and each one is guarded to be as
  non-destructive as the design allows (see "Promotion" above).
- **The employee id is immutable and is the partition key** (`EMP#<employeeId>`) — never a
  generated UUID. `common/keys.py` is the one place the key string is built; nothing else should
  concatenate `EMP#` or name the key attribute directly.
- **No mock fallback**: the frontend has no local/hardcoded employee data left anywhere. If a fetch
  fails, the UI shows an error — there is nothing else it could honestly show.

## Known gaps (deliberately deferred, not overlooked)

- No un-something for a full onboarding delete-and-recreate; only promotion has an undo, and only
  for 7 days.
- No server-side compensation/rollback for an interrupted promote/un-promote/reassign sequence.
- Work-email uniqueness is not enforced (was, via a second guard item, before the table went to one
  item per employee).
- SNS/SQS onboarding triggers (notifications, IT setup request) mentioned in the original brief are
  not started.

## Note

`~/.codex` exists on this machine (an OpenAI Codex config). It was not read or imported while
writing this file, per this project's import policy — reply `/import` if you want it scanned for
importable MCP servers, slash commands, or instructions.
