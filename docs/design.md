# Design notes

Everything about how this thing is built and why. For getting it running, see the
[README](../README.md).

- [Architecture](#architecture)
- [Data model](#data-model)
- [Phase 1 — UI shell](#phase-1--ui-shell)
- [Phase 2 — the backend](#phase-2--the-backend)
- [Phase 3 — integration](#phase-3--integration)
- [Design decisions worth knowing](#design-decisions-worth-knowing)
- [Status of testing](#status-of-testing)
- [Not built yet](#not-built-yet)
- [Data-model decisions from the review](#data-model-decisions-from-the-review)

---

## Architecture

```
browser (vanilla JS, no build step)
    |  fetch()
    v
API Gateway  /dev
    |
    +-- GET    /employees                                ListEmployeesFunction
    +-- POST   /employees                                CreateEmployeeFunction
    +-- GET    /employees/{id}                           GetEmployeeFunction
    +-- PUT    /employees/{id}                           UpdateEmployeeFunction
    +-- DELETE /employees/{id}                           DeleteEmployeeFunction
    +-- PATCH  /employees/{id}/checklist/{itemId}        SetChecklistItemFunction
                                |
                                v
                    DynamoDB  onboarding-dev  (single table, PK + SK)
```

### Files

```
index.html          page shell, #app mount point, #app-error banner slot
css/styles.css      structure only, no visual polish
js/
  config.js         API_BASE_URL - the one value that changes per environment
  store.js          six fetch() calls to API Gateway  <-- the only data source
  ui.js             pure render functions (state in, HTML string out)
  app.js            hash router, event wiring, validation, error handling

template.yaml       SAM: DynamoDB table, six Lambdas, API Gateway
samconfig.toml       committed, so `sam deploy` needs no arguments
src/
  common/           keys, db clients, validation, reads, response helpers, checklist template
  handlers/         one file per route
tests/              pytest - pure logic plus the handlers against in-memory DynamoDB
scripts/
  seed_employees.py wipe + repopulate the table through the API
events/             sample API Gateway payloads for `sam local invoke`
docs/
  api.md            endpoint reference + the acceptance run
  design.md         this file
  phase3-testing.md the manual click-through
  Employee-Onboarding.postman_collection.json
```

The frontend uses classic `<script>` tags sharing a `window.App` namespace rather than ES modules.
That started as a way to keep `index.html` openable from the filesystem; since Phase 3 the page has
to be served over http anyway, but there's no reason to churn it for a project with no bundler.
Load order is load-bearing — `app.js` captures `App.store` and `App.ui` when its IIFE runs, so it
comes last.

Routes are linkable and the back button works:

```
#/employees                      employee list
#/employees/new                  add form
#/employees/:id/edit             edit form
#/employees/:id/checklist        onboarding checklist
```

---

## Data model

What the API returns and the UI renders:

```js
{
  id: "4d4494cd-589c-49b3-93ff-2708b1b26656",   // server-generated UUID
  firstName, lastName, email, phone,
  department,        // Engineering | HR | Finance | Operations
  jobTitle,
  manager,
  startDate,         // "2026-09-01"
  employmentType,    // Full-time | Contract | Intern
  checklist: [ { id, label, owner, done, comment }, ... ],  // 8 items

  status,            // derived server-side, additive
  progress           // { done, total, percent } - also derived, also additive
}
```

The default checklist: offer letter signed, ID proof submitted, bank details collected, laptop
issued, email/AD account created, building access card issued, induction session attended, policy
acknowledgement signed.

`status` and `progress` are computed on every read and never stored — and computed in exactly one
place, `common/models.py`. The frontend renders what the API sends and no longer derives its own;
the two copies drifted once already (the rounding defect below), which is the whole argument.

---

## Phase 1 — UI shell

| Brief task | Status |
|---|---|
| 1. Employee list view | Done — table, live search, department + status filters |
| 2. Add Employee form | Done — validation, persists via the API since Phase 3 |
| 3. Edit Employee view | Done — pre-filled form, plus delete |
| 4. Onboarding checklist per employee | Done — 8 items, progress bar, derived status |
| 5. Review with Santosh | Done |

---

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
`App.store.setChecklistItem` already expected and Phase 3 needed.

### The table

One DynamoDB table, `PK` + `SK`, nine items per employee:

```
PK = EMP#<uuid>   SK = PROFILE        the employee record
PK = EMP#<uuid>   SK = CHK#<itemId>   one row per checklist item, 8 of them

PK = EMAIL#<lowercased address>   SK = EMAIL    uniqueness guard, one per employee
```

**Why the email guard is an item and not a check.** DynamoDB can only enforce uniqueness on the
partition key, and the partition key here is a UUID. So "one employee per work email" is enforced
by a second item, keyed on the address, written in the *same transaction* as the profile with
`attribute_not_exists(PK)`. Reading first and then writing is a race that two simultaneous POSTs
will win. The guard travels with its employee: created with them, moved when the address is edited,
deleted when they are — miss any of those and an address is either duplicated or locked forever.
It lives outside the employee's partition, so `list_employees` filters it out by prefix.

One caveat for the records already in the dev table: they predate the guard and have none, so their
addresses are not reserved until each is edited or recreated. `py scripts/seed_employees.py --wipe
--seed` regenerates the six through the API and gives them guards.

**Why the checklist is separate rows rather than a nested list.** This is also what made HR
comments a one-attribute change rather than a feature: the note belongs to one step, so it lives on
that step's row and is written by the same `UpdateItem` as the tick. Ticking a box becomes an
`UpdateItem` against one small item — no read-modify-write of a list, so two people ticking
different boxes at the same time can't clobber each other. Reading is still one round trip: a
single `Query` on the partition key returns the profile and all eight rows together.

**Why the partition key is a UUID and not `emp-001` or the email address.** Email is editable and
partition keys are immutable. A sequential counter needs a counter item updated on every create — a
serialised hot key bought purely for cosmetics. Nothing in the frontend parses the id, so the
`emp-001` format was simply dropped.

**Scan vs Query for `GET /employees`, since the brief asks.** `Query` needs a partition key, and
employees are spread across every partition by design — so `Scan` is the correct operation, not a
workaround. The usual counter-suggestion is a sparse GSI with a constant partition key so it becomes
a `Query`; here that's strictly worse, because the list view filters by derived status and draws a
progress bar, both of which need the checklist. A profile-only index would give you N profiles and
then N follow-up queries. One `Scan` already returns everything.

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
- **`status` is derived, never stored** — enforced in `common/models.py:derive_status`, and
  computed nowhere else. The list filter runs over the `status` the API returned.
- **Read-after-write is consistent, read-only isn't.** `PUT` and `PATCH` re-read the partition to
  build their response, and a Query is eventually consistent by default — so both pass
  `ConsistentRead=True` via `common/repository.py`. `GET` doesn't: nothing it returns was written a
  millisecond earlier by the same caller, and a strong read costs twice as much.
- **Cancelled transactions are read, not guessed.** `CancellationReasons` says which condition
  failed, so a duplicate email returns `409` on the email and a UUID collision returns `409` on the
  id — and a throttle, which arrives the same way, is not reported as either.
- **Six functions, one shared `CodeUri`.** Separate functions give per-route IAM and per-route log
  groups; the shared `CodeUri` means `common/` is packaged into each one without a Lambda layer.
- **IAM written out inline, not via SAM policy templates.** The templates are coarser than they
  look — `DynamoDBReadPolicy` grants Scan to a handler that only needs Query, and
  `DynamoDBWritePolicy` grants no read actions at all, which would push the delete handler up to
  full CRUD.
- **No DynamoDB Local.** It would add an `endpoint_url` branch that exists only for the emulator,
  and it never exercises the thing most likely to break here, which is IAM. Offline testing is
  `pytest` over the pure logic; everything else runs against a real dev stack.
- **No Docker.** `src/requirements.txt` is empty — boto3 ships in the Lambda runtime and nothing
  here has C extensions — so SAM builds natively against a local Python 3.13.

---

## Phase 3 — integration

| Brief task | Status |
|---|---|
| 1. Replace the hardcoded list with a live API call | Done — CORS was already configured in Phase 2, so it wasn't the usual trap |
| 2. Wire up Add/Edit/Delete to real endpoints | Done — data survives a refresh |
| 3. Wire the checklist to real backend state | Done — one `PATCH` per tick |
| 4. Test the full flow end to end | Done — see [Status of testing](#status-of-testing) |

### No mock data in the frontend

The rule this phase enforces: **there is no employee array anywhere in `js/`.** `data.js` first
became `model.js` — two dropdown enums and four pure display helpers, no records — and has since
gone entirely. What was left in it was a second implementation of things `common/models.py` already
owns, and the two had drifted once. The enums and the status/progress rules now exist only on the
server; `js/ui.js` keeps the presentation helpers that have no server-side counterpart to disagree
with.

The dropdowns are built from the values the loaded employees actually carry (`setEmployees` in
`app.js`). The trade is deliberate and visible: a department nobody is in yet is not offered, and on
an empty table the form falls back to text inputs so the first hire is still creatable. The server
validates against the real enum in every case and names the bad field in its `400`.

The Phase 1 fixtures still exist — as `scripts/seed_employees.py`, which POSTs the same six people
*into DynamoDB* over the real API. Same test data, other side of the wire.

The check that this actually holds: turn the network off and reload. The list must be empty with an
error on it. If six people appear, something is still reading from local state.

### Checklist comments

HR needs somewhere to put "chased payroll twice, still no bank details". One note per checklist
item, editable and clearable, capped at 500 characters.

- **No new endpoint.** `PATCH /employees/{id}/checklist/{itemId}` already existed for the tick, and
  PATCH means *change the keys I sent*. `{"done": true}` leaves the comment alone, `{"comment": "…"}`
  leaves the tick alone, both together work, and `{}` is a `400` rather than a silent no-op.
- **One note, not a thread.** A thread needs an author, and there is no authentication in this stack
  to supply one. A list of anonymous comments is worse than a single note that whoever is looking
  after this hire keeps current.
- **Cleared means removed.** An empty comment `REMOVE`s the attribute rather than storing `""`, so
  the two states in the table are "has a note" and "has no attribute" rather than three.
- **The draft survives a repaint.** Every tick re-renders the whole checklist from the server's
  response, which would throw away a half-typed comment. The open editor is state in `app.js`
  (`commentEditor = { itemId, draft }`), not in the DOM, so it is re-rendered rather than lost —
  including its text and the cursor position at the end of it.
- **A failed save keeps the text.** No repaint on failure, and an over-long comment paints its
  message under the box rather than in the page banner, because it is a problem with that one field.
- **The icon is a toggle.** One control, so a second press closes what the first press opened, and
  its tooltip and `aria-label` change to say so. Discarding unsaved text is confirmed first here and
  when switching to another item's box — but not for Cancel or Esc, which already say "discard" in
  as many words.

### Accessibility

Every view is built by replacing the `innerHTML` of `#app`. No page load happens, which means a
screen reader is told nothing and focus stays on a control that no longer exists — the standard
single-page failure. Four things fix it, all in `app.js`:

- **`paint()`** swaps the view and clears `aria-busy`; `showLoading()` sets it, so "Loading…" is
  announced as a wait rather than as the answer.
- **`focusHeading()`** moves focus to the new view's `<h1>` (which carries `tabindex="-1"`), so the
  next Tab starts inside the content that just arrived.
- **`announce()`** writes to `#app-status`, a polite live region that lives *outside* `#app`. That
  placement is the whole trick: a live region inserted at the same moment as its text is usually
  not announced at all.
- **`setTitle()`** names each route, which is what a tab, a history entry and a screen reader all
  read.

The checklist needed one more. A tick repaints the entire view, throwing away the checkbox the user
is standing on, so `paintChecklist(employee, keepFocusOn)` puts focus back on the box that changed —
a keyboard user can now work down the list instead of being dumped at the top on every tick. The
failure path had the same bug from a different direction: disabling a focused element hands focus to
`<body>`, so a failed tick silently ejected you. It now takes focus back, but only if it is still on
`<body>` — if you have clicked elsewhere in the meantime, you stay there.

The rest is unglamorous and cheap: `role="progressbar"` with real `aria-valuenow` (a div's width is
invisible), `aria-label` on each row action so "Edit" six times over says *who* it edits,
`scope="col"` on the headers and a `sr-only` name on the actions column, `aria-describedby` +
`aria-invalid` wiring every input to its own error text, `aria-live="polite"` on the record count so
filtering announces the new total, `:focus-visible` rings that keyboard users get and mouse users
don't, and Escape to dismiss the error banner.

### Errors are the actual work

Swapping six function bodies for `fetch()` took an afternoon. The rest of the phase is that network
calls fail and Phase 1 had no way to say so — not one `.catch` in the codebase, so a rejected
promise left a stale view on screen and a warning in a console nobody had open.

- `store.js` has one private `request()` helper. Everything non-2xx rejects with an Error carrying
  `status`, `code` and `fields`, so callers branch on the status rather than on message text.
- Two handlers, picked by who owns the screen: **`failLoad()`** for loads, which must also replace
  the "Loading…" placeholder so the page stops implying it's still trying; **`showError()`** for
  actions, where the view is already up and stays usable.
- The banner lives *outside* `#app`. Every render replaces the whole of `#app`, and the failure this
  most needs to report is the one where no view rendered at all.
- Server-side 400s reuse the client-side field painter unchanged, because `common/models.py` was
  written to mirror `validate()` in `app.js` — same field names, same wording. Server validation
  lands under the right input with no new UI.
- `getEmployee` is the one function that resolves `null` on a 404 instead of rejecting. A stale
  bookmark to a deleted employee is a normal thing to have, and the router renders "Not found" for
  it. The special case sits on that function rather than as a flag on `request()`.
- Ticking a checkbox repaints from the `PATCH` response instead of re-fetching — one request per
  tick, not two. On failure the box **snaps back**, which is what "the checkbox is never the source
  of truth" was always supposed to mean.

---

## Design decisions worth knowing

### `store.js` is the API seam — and it paid off

Phase 1 routed every read and write through `App.store`, made every function return a Promise, and
handed callers copies rather than live references. That was ceremony at the time, on the bet that it
was the same contract a real HTTP API would give us.

It held. Phase 3 replaced all six bodies with `fetch()` calls and **`ui.js` needed no changes to any
existing view** — only three new functions for the banner and placeholders. The work that did land
in `app.js` was error handling, which is genuinely new behaviour rather than a refactor.

### Status is computed, not stored

There is no stored `status` field. It's derived from the checklist every time: 0 items done →
Pending, all done → Onboarded, otherwise In Progress. It cannot drift out of sync with the thing it
describes.

**Open question for review:** if HR needs to set status manually (on hold, offer withdrawn), it
stops being derivable and becomes a real stored field.

### The checkbox is never the source of truth

Toggling a checklist item writes through the store, then re-renders from what the store returns. The
DOM is always downstream of state. Phase 3 completed the thought: on a failed write the box snaps
back, rather than sitting there ticked and unpersisted.

### Add and Edit share one render function

`ui.formView(employee)` renders both, parameterised by an optional employee. Two near-identical
forms drift apart; one form can't.

---

## Status of testing

**Backend, offline:** `py -m pytest` — 125 tests, green. Validation and derived status as pure
functions, plus all six handlers driven end to end against an in-memory DynamoDB (moto).

`tests/test_models.py` covers the pure logic — validation, derived status, the write whitelist, the
DynamoDB-item to API-object mapping. `tests/test_handlers.py` drives all six handlers, including the
acceptance run from `docs/api.md`.

That second layer is not a substitute for deploying — moto emulates the DynamoDB API, not IAM, and
IAM is the likeliest thing to be wrong on a first deploy. It is very good at catching the tedious
things: it already found one real bug, where `boto3.resource(...).meta.client` silently
double-serialised the `TransactWriteItems` payload and made every create fail.

**Backend, deployed:** green. The full acceptance run in [api.md](api.md) was executed against
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

Also confirmed: editing an employee left their ticked checklist item intact; a table scan after the
delete showed **zero** orphan `CHK#` rows; a record created earlier was still readable from a
separate process minutes later; and every response carries the CORS headers.

One defect was found and fixed: `progress.percent` used Python's `round()`, which is banker's
rounding, so a 1-of-8 checklist reported 12% while the UI's `Math.round` said 13%.
`common/models.py` now uses `int(x + 0.5)` to match JavaScript, with a regression test covering all
nine steps from 0/8 to 8/8. That defect is also the reason the frontend no longer computes a
percentage at all — two implementations of one rule is the bug, and the fix above only patched this
instance of it.

**Four correctness fixes after the Phase 3 review**, each with tests:

| | |
|---|---|
| Stale read-after-write | `PUT` and `PATCH` re-read with `ConsistentRead=True`. An eventually consistent Query could return the pre-write profile, and the checklist view repaints straight from that response |
| Dates that aren't dates | `2026-13-45` and `2026-02-30` matched the `YYYY-MM-DD` regex and were stored. Now parsed as well as matched |
| Duplicate work emails | Nothing stopped two employees sharing one. Now a uniqueness guard item written in the same transaction |
| Misreported `409` | Every cancelled transaction claimed "that employee id already exists". Now read from `CancellationReasons` |

**Frontend:** run in a real browser (headless Chrome against `py -m http.server 8000`) and verified
against the live API. Re-run after the enums were removed: all six rows render, the department and
employment-type dropdowns build themselves from the loaded records, and the status filter orders
itself Pending → In Progress → Onboarded. Against a stub API returning zero employees, the form
degrades to text inputs rather than to empty dropdowns nobody can submit.

The store was exercised end to end through the real `App.store` — 23 assertions, all passing,
including the ones easy to get wrong:

- `DELETE` returns 204 with an empty body, which `response.json()` would throw on
- `getEmployee` resolves `null` on a 404 while `updateEmployee` and `deleteEmployee` reject on one
- a 400 arrives with a `fields` map whose keys match the form inputs exactly
- `id` and `checklist` smuggled into a request body are stripped before sending
- the client's `computeStatus` / `progress` agree with the server's on the same record

Rendering was verified from the returned DOM: six rows with UUID ids, all three status badges,
progress bars at 100/63/75/25/0/0%, and a deep-link straight to `#/employees/<uuid>/checklist`
resolving correctly — proof the router handles UUIDs.

The failure path was forced by pointing the API hostname at a dead port. Result: the banner reads
"Could not reach the server", `#app` reads "Could not load employees", and — the check that matters
— **no employee renders at all**. Console output on failure is exactly one deliberate
`console.error`; no `Uncaught (in promise)`.

**Not automated.** There is no JS test runner in this project and adding npm was out of scope, so
none of the frontend checks run in CI. [phase3-testing.md](phase3-testing.md) is the repeatable
version.

---

## Not built yet

- **Document upload** (offer letter, ID proof) — a visible stub sits on the checklist page marking
  where it goes; the storage itself is S3 in a later phase.
- **Who wrote a comment, and when** — the item stores `updatedAt`, but with no authentication there
  is no author to record and the API does not return either. Revisit alongside auth.
- **Auth** — no login, no roles, and the API is public. The `owner` field on checklist items is
  display-only. Don't put real employee data in the dev stack.
- **Automated onboarding triggers** — notifications, IT setup request, HR alert. SNS/SQS, Phase 4.
- **Pagination on `GET /employees`** — the Scan follows `LastEvaluatedKey` internally and returns
  everything in one response. Fine at this scale; revisit alongside the GSI if it ever isn't.

---

## Data-model decisions from the review

Phase 2 answered these five by assumption to keep moving, and they were taken to the review with
Santosh. They're recorded here because each one is cheap to change early and expensive once the
table holds real records — if any of them is revisited later, this is the list to revisit.

| Question | Decision |
|---|---|
| 1. Is the checklist fixed for everyone, or does it vary by department/role? | Fixed — the same 8 items |
| 2. Should IT-owned items be tickable by HR? | Yes; `owner` is display-only, no permissions |
| 3. Are employees hard-deleted or deactivated? | Hard-deleted, whole partition at once |
| 4. Should the list default to in-progress hires only? | No — returns everyone |
| 5. Does status need a manual override? | No — still derived from the checklist |

Number 5 is the one to watch. Reversing it turns `status` from a derived value into a stored field
that can drift out of sync with the checklist — which the current design makes impossible.
