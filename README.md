# Employee Management & Onboarding System

A lightweight internal tool for Breville HR/Admin users to manage employee records and
track onboarding progress for new hires.

**Current state: Phase 2 — CRUD backend deployed to AWS and verified end to end.**
The Phase 1 UI still runs on mock data; connecting the two is Phase 3.

Live API: `https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev`
(stack `onboarding-system-dev`, region `eu-north-1`)

Made by Abhishek.

---

## Running it

### The UI (Phase 1)

Open `index.html` in a browser. That's the whole setup — no install, no build step,
no server.

```
start index.html
```

Data lives in memory only: a refresh resets everything back to the six seeded
employees. This is deliberate for Phase 1 — see [Why no localStorage](#why-no-localstorage).

## What works

| Brief task | Status |
|---|---|
| 1. Employee list view | Done — table, live search, department + status filters |
| 2. Add Employee form | Done — validation, adds to the in-memory list |
| 3. Edit Employee view | Done — pre-filled form, plus delete |
| 4. Onboarding checklist per employee | Done — 8 items, progress bar, derived status |
| 5. Review with Santosh | Pending |

Phase 2 status is in [Phase 2 — the backend](#phase-2--the-backend) below.

Routes are linkable and the browser back button works:

```
#/employees                      employee list
#/employees/new                  add form
#/employees/:id/edit             edit form
#/employees/:id/checklist        onboarding checklist
```

## Project structure

```
index.html          page shell + #app mount point
css/styles.css      ~60 rules, structure only
js/
  data.js           mock employees, checklist template, model helpers
  store.js          async CRUD  <-- the seam that becomes the real API
  ui.js             pure render functions (state in, HTML string out)
  app.js            hash router, event wiring, form validation

template.yaml       SAM: DynamoDB table, six Lambdas, API Gateway
src/
  common/           keys, db clients, validation, response helpers, checklist template
  handlers/         one file per route
tests/              pytest - pure logic plus the handlers against in-memory DynamoDB
events/             sample API Gateway payloads for `sam local invoke`
docs/
  api.md            endpoint reference + the acceptance run
  Employee-Onboarding.postman_collection.json
```

Scripts are classic `<script>` tags sharing a `window.App` namespace rather than ES
modules, because browsers block module imports over `file://` and the point of Phase 1
is that `index.html` opens by double-clicking it.

## Data model

```js
{
  id: "emp-001",
  firstName, lastName, email, phone,
  department,        // Engineering | HR | Finance | Operations
  jobTitle,
  manager,
  startDate,         // "2026-09-01"
  employmentType,    // Full-time | Contract | Intern
  checklist: [ { id, label, owner, done }, ... ]   // 8 items
}
```

The default checklist: offer letter signed, ID proof submitted, bank details collected,
laptop issued, email/AD account created, building access card issued, induction session
attended, policy acknowledgement signed.

## Phase 2 — the backend

Built and tested on its own, deliberately not wired to the UI. That isolation is the point: a bug
found here is a backend bug, with no frontend to blame.

| Brief task | Status |
|---|---|
| 1. Design the employee record structure | Done — single-table DynamoDB, see below |
| 2. Lambda: create employee | Done — `src/handlers/create_employee.py` |
| 3. Lambda: get employee by ID | Done — `src/handlers/get_employee.py` |
| 4. Lambda: list all employees | Done — `src/handlers/list_employees.py` (Scan; reasoning below) |
| 5. Lambda: update employee | Done — `src/handlers/update_employee.py` |
| 6. Lambda: delete employee | Done — `src/handlers/delete_employee.py` |
| 7. Expose as REST endpoints | Done — `template.yaml`, one route per operation |
| 8. Test via Postman/curl | Done — full acceptance run green against the deployed stack |

Plus `PATCH /employees/{id}/checklist/{itemId}`, which isn't in the brief's table but which
`App.store.setChecklistItem` already expects and Phase 3 needs.

### Deploying

Requires the [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
and AWS credentials.

Already deployed; `samconfig.toml` is committed so these need no arguments:

```bash
sam validate --lint
sam build
sam deploy
sam sync --watch             # ~5s code pushes while iterating
sam delete                   # tear the whole stack down
```

**No Docker required.** `src/requirements.txt` is empty — boto3 ships in the Lambda runtime and
nothing here has C extensions — so SAM builds natively against a local Python 3.13. Docker would
only be needed for `sam local` or for `--use-container`, neither of which this project uses.

The `ApiBaseUrl` output is the value Phase 3 drops into `js/store.js`. `sam delete` tears it all
down.

Offline, with no AWS at all:

```bash
py -m pip install -r requirements-dev.txt
py -m pytest        # 78 tests, ~15 seconds
```

`tests/test_models.py` covers the pure logic — validation, derived status, the write whitelist,
the DynamoDB-item to API-object mapping. `tests/test_handlers.py` drives all six handlers end to
end against an in-memory DynamoDB (moto), including the acceptance run from `docs/api.md`.

That second layer is not a substitute for deploying — moto emulates the DynamoDB API, not IAM, and
IAM is the likeliest thing to be wrong on a first deploy. It is very good at catching the tedious
things: it already found one real bug, where `boto3.resource(...).meta.client` silently
double-serialised the `TransactWriteItems` payload and made every create fail.

### The table

One DynamoDB table, `PK` + `SK`, nine items per employee:

```
PK = EMP#<uuid>   SK = PROFILE        the employee record
PK = EMP#<uuid>   SK = CHK#<itemId>   one row per checklist item, 8 of them
```

**Why the checklist is separate rows rather than a nested list.** Ticking a box becomes an
`UpdateItem` against one small item — no read-modify-write of a list, so two people ticking
different boxes at the same time can't clobber each other. Reading is still one round trip: a
single `Query` on the partition key returns the profile and all eight rows together.

**Why the partition key is a UUID and not `emp-001` or the email address.** Email is editable and
partition keys are immutable. A sequential counter needs a counter item updated on every create —
a serialised hot key bought purely for cosmetics. Nothing in the frontend parses the id, so the
`emp-001` format is simply dropped.

**Scan vs Query for `GET /employees`, since the brief asks.** `Query` needs a partition key, and
employees are spread across every partition by design — so `Scan` is the correct operation, not a
workaround. The usual counter-suggestion is a sparse GSI with a constant partition key so it
becomes a `Query`; here that's strictly worse, because the list view filters by derived status and
draws a progress bar, both of which need the checklist. A profile-only index would give you N
profiles and then N follow-up queries. One `Scan` already returns everything.

Scale, so it's an argument and not a vibe: ~9 items × ~300 bytes ≈ 2.7 KB per employee, so a 1 MB
`Scan` page holds ~370 of them. Fine for hundreds, wrong for a million — at which point you
denormalise a `doneCount` onto the profile and add that index. The rule underneath: **`Query` when
you know the partition key, `Scan` only when you genuinely need every item.**

### Choices worth defending in review

- **Atomic writes.** Create writes all 9 items with `TransactWriteItems`, delete removes all 9 the
  same way. `BatchWriteItem` would be cheaper and wrong: it isn't atomic, so a partial failure
  leaves an employee holding 5 of 8 checklist rows.
- **Condition expressions everywhere.** `UpdateItem` upserts by default, so without
  `attribute_exists(PK)` a `PUT` to a deleted id would silently resurrect a profile with no
  checklist behind it. Update, patch and delete all guard against it and return `404` instead.
- **`status` is still derived, never stored** — same invariant as Phase 1, now enforced in
  `common/models.py:derive_status` instead of `js/data.js`.
- **Six functions, one shared `CodeUri`.** Separate functions give per-route IAM and per-route log
  groups; the shared `CodeUri` means `common/` is packaged into each one without a Lambda layer.
- **IAM written out inline, not via SAM policy templates.** The templates are coarser than they
  look — `DynamoDBReadPolicy` grants Scan to a handler that only needs Query, and
  `DynamoDBWritePolicy` grants no read actions at all, which would push the delete handler up to
  full CRUD.
- **No DynamoDB Local.** It would add an `endpoint_url` branch that exists only for the emulator,
  and it never exercises the thing most likely to break here, which is IAM. Offline testing is
  `pytest` over the pure logic; everything else runs against a real dev stack.

### `js/store.js` is untouched, on purpose

Phase 2 changes no frontend behaviour at all. The API response shape was designed backwards from
`js/data.js` so that Phase 3 only has to swap the six function bodies in `js/store.js` for
`fetch()` calls — `js/ui.js` and `js/app.js` don't change.

The one check for that: paste a `GET /employees/{id}` response into the browser console on the
Phase 1 page and call `App.computeStatus(r)` and `App.progress(r)`. Both work unmodified.

## Design decisions worth knowing

### `store.js` is the API seam

Every read and write goes through `App.store`, and every function there is already
`async` with every caller already awaiting it. Today the bodies operate on an in-memory
array. In Phase 3 they become `fetch()` calls to API Gateway and **nothing outside that
one file changes.**

Callers also get deep copies, never live references — the only way to change stored data
is through a store function, which is the same contract a real HTTP API gives you. Some
ceremony now buys no surprise refactors later.

### Status is computed, not stored

There is no `status` field on an employee. It's derived from the checklist every time
it's displayed: 0 items done → Pending, all done → Onboarded, otherwise In Progress.
It cannot drift out of sync with the thing it describes.

**Open question for review:** if HR needs to set status manually (on hold, offer
withdrawn), it stops being derivable and becomes a real field in Phase 2.

### The checkbox is never the source of truth

Toggling a checklist item writes to the store, then re-renders the view from what the
store returns. The DOM is always downstream of state. This is the habit that makes the
backend swap uneventful rather than a rewrite.

### Add and Edit share one render function

`ui.formView(employee)` renders both, parameterised by an optional employee. Two
near-identical forms drift apart; one form can't.

### Why no localStorage

Persistence now would create a stale-cache problem to unwind the moment the real API
arrives. Refresh-resets-to-mock-data is the honest Phase 1 behaviour.

## Not built yet

- **UI/backend integration** — the UI still runs on mock data. Phase 3.
- **Document upload** (offer letter, ID proof) — a visible stub sits on the checklist
  page marking where it goes; the storage itself is S3 in a later phase.
- **Auth** — no login, no roles, and the API is public. The `owner` field on checklist items is
  display-only. Don't put real employee data in the dev stack.
- **Automated onboarding triggers** — notifications, IT setup request, HR alert. These
  are SNS/SQS in a later phase.
- **Pagination on `GET /employees`** — the Scan follows `LastEvaluatedKey` internally and returns
  everything in one response. Fine at this scale; revisit alongside the GSI if it ever isn't.

## Questions for the review with Santosh

Phase 2 answered these by assumption to keep moving. Each one is cheap to change now and
expensive once the table holds real records, so they're still worth settling:

| Question | Assumed |
|---|---|
| 1. Is the checklist fixed for everyone, or does it vary by department/role? | Fixed — the same 8 items |
| 2. Should IT-owned items be tickable by HR? | Yes; `owner` is display-only, no permissions |
| 3. Are employees hard-deleted or deactivated? | Hard-deleted, whole partition at once |
| 4. Should the list default to in-progress hires only? | No — returns everyone |
| 5. Does status need a manual override? | No — still derived from the checklist |

A "yes" to 5 is the expensive one: it turns `status` from a derived value into a stored field that
can drift, which the design currently makes impossible.

## Status of testing

**Backend, offline:** `py -m pytest` — 78 tests, green. Validation and derived status as pure
functions, plus all six handlers driven end to end against an in-memory DynamoDB.

**Backend, deployed:** green. The full acceptance run in `docs/api.md` was executed against
`onboarding-system-dev` in `eu-north-1`:

| Step | Expected | Got |
|---|---|---|
| create | 201 | 201 |
| list | 200 | 200 |
| get | 200 | 200 |
| tick a checklist item | 200 | 200 |
| tick an unknown item | 404 | 404 |
| invalid email | 400 | 400 |
| update | 200 | 200 |
| delete | 204 | 204 |
| get after delete | 404 | 404 |
| update after delete | 404 | 404 |

Also confirmed: editing an employee left their ticked checklist item intact; a table scan after
the delete showed **zero** orphan `CHK#` rows; a record created earlier was still readable from a
separate process minutes later; every response carries the CORS headers; and a `GET /employees/{id}`
response has exactly the Phase 1 field set (plus the additive `status` and `progress`), with
`App.progress` and `App.computeStatus` producing the same answers as the server.

One defect was found by that last check and fixed: `progress.percent` used Python's `round()`,
which is banker's rounding, so a 1-of-8 checklist reported 12% while the UI's `Math.round` said
13%. `common/models.py` now uses `int(x + 0.5)` to match JavaScript, with a regression test
covering all nine steps from 0/8 to 8/8.

**UI:** still not run in a browser — reviewed by hand, not executed. Worth clicking through all
four views before the review meeting.
