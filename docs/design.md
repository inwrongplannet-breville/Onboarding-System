# Design notes

Everything about how this thing is built and why. For getting it running, see the
[README](../README.md).

- [Architecture](#architecture)
- [Data model](#data-model)
- [Database design](database-design.md) — separate file: the storage layout in full
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
                    DynamoDB   one item per employee, keyed by employeeKey
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
  database-design.md  the DynamoDB layout, access patterns and invariants
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
  id: "E1024",       // the employee number HR typed; also the partition key
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
| 1. Design the employee record structure | Done — one DynamoDB item per employee, see below |
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

One DynamoDB table, `employeeKey` only, **one item per employee** — and employees are the only kind of item
in it:

```
employeeKey = EMP#<employeeId>
{
  ...the nine editable profile fields, plus createdAt / updatedAt,
  plus archivedAs / archivedAt once archived,

  checklist: [                              <- 8 entries, in template order
    {itemId, label, owner, order, done, comment?, updatedAt},
    ...
  ]
}
```

~1.2 KB per employee, ~0.3% of the 400 KB item limit. No sort key, no secondary indexes.

Three things a reviewer will want to know straight away:

- **Ticking a box is still a single server-side `UpdateItem`, not a read-modify-write.** An entry is
  addressed by a nested document path, `checklist[i].done`, so two simultaneous PATCHes cannot lose
  each other's change — even on the same entry.
- **Work email uniqueness is no longer enforced.** The guard item that held it needed a second item
  in the table, and there isn't one. Two employees may share an address, and `POST`/`PUT` no longer
  return `409` for a duplicate.
- **The partition key is the employee number** HR types on the create form — `EMP#E1024`. It used
  to be a UUID. Moving a meaningful id into the key is what makes employee numbers unique:
  DynamoDB can enforce uniqueness on a partition key and on nothing else, so the create-time
  `attribute_not_exists(employeeKey)` went from guarding against a collision nobody would ever see to
  being the constraint itself. The costs are real and accepted — the number can never be edited
  (an `UpdateItem` cannot move an item between partitions), it has to be case-folded on the way
  in or `e1024` and `E1024` become two people, and archiving keeps the number taken. A
  sequential counter would have given readable ids too, but at the price of a serialised hot
  key on every hire and with no HR-meaningful number in it.

**Full detail — item shape, access patterns per route, the index invariants that must not be broken,
cost measurements, and the deployment constraint on key-schema changes — is in
[database-design.md](database-design.md).** That file is the authoritative description; this section
is a summary.

### Choices worth defending in review

- **No transactions anywhere.** An employee is one item, so create is a single conditional
  `PutItem` and every other write is a single `UpdateItem`. There is nothing left to keep atomic
  across items — which retired `TransactWriteItems`, the low-level boto3 client, and the
  `CancellationReasons` demux along with it.
- **Archiving is enforced, not requested.** The archive stamp and the checklist live on the same
  item, so `PATCH` carries `attribute_not_exists(archivedAs)` in its own `ConditionExpression`
  rather than as a `ConditionCheck` on a sibling row. "An archived record is frozen" is still a
  property of the table rather than a check someone remembered to write.
- **Condition expressions everywhere.** `UpdateItem` upserts by default, so without
  `attribute_exists(employeeKey)` a `PUT` to an unknown id would conjure a half-employee with no checklist
  behind it. Update, patch and delete all guard against it and return `404` instead.
- **`status` is derived, never stored** — enforced in `common/models.py:derive_status`, and
  computed nowhere else. The list filter runs over the `status` the API returned.
- **Read-after-write is consistent, read-only isn't.** `PUT` and `PATCH` re-read to build their
  response, and reads are eventually consistent by default — so both pass `ConsistentRead=True` via
  `common/repository.py`. `GET` doesn't: nothing it returns was written a millisecond earlier by the
  same caller, and a strong read costs twice as much.
- **A failed condition is read back, not guessed.** `PUT` and `PATCH` re-read on
  `ConditionalCheckFailed` to say whether it was `404` or `409`, because telling a caller the wrong
  one sends them looking in the wrong place. In `PATCH` there is a third outcome: an employee who
  exists and is active means the stored list has drifted from the template, which is a corrupted
  record rather than a bad request — so it raises and becomes a `500` with a trace, instead of
  hiding behind a `404` forever.
- **`PUT` is protected by a whitelist, not by a separate row.** The `SET` clause is built from
  `EDITABLE_FIELDS` and never names `checklist`. That used to be belt-and-braces on top of "the
  checklist is a different item"; it is now the only thing standing between a profile edit and eight
  erased ticks. `test_update_preserves_checklist_progress` is the regression net.
- **Six functions, one shared `CodeUri`.** Separate functions give per-route IAM and per-route log
  groups; the shared `CodeUri` means `common/` is packaged into each one without a Lambda layer.
- **IAM written out inline, not via SAM policy templates.** The templates are coarser than they
  look — `DynamoDBReadPolicy` grants Scan to a handler that only needs `GetItem`, and
  `DynamoDBWritePolicy` grants no read actions at all, which would push the delete handler up to
  full CRUD. `Query` appears in no role now; reading an employee is a `GetItem`.
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
- **One note, not a thread.** A thread needs an author, and when this was built there was no
  authentication to supply one. There is now — the authorizer puts a username on every request —
  but the decision stands on its own: a list of comments is a conversation, and what HR needs on
  "chase the bank details" is one current answer, not a history of who chased. Only officials see
  these at all; the employee response omits the checklist entirely.
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
| archive | 200 | 200 |
| get after archive | 200 | 200 |
| update after archive | 409 | 409 |

Also confirmed: editing an employee left their ticked checklist item intact; a record created
earlier was still readable from a separate process minutes later; and every response carries the
CORS headers. (The delete rows in that table were re-run after DELETE became an archive; the earlier
Phase 2 run showed `204 / 404 / 404` against the hard delete it replaced.)

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
| Duplicate work emails | Nothing stopped two employees sharing one. Fixed then with a uniqueness guard item — and **reverted** when the table collapsed to one item per employee, since a guard needs a second item. Duplicates are possible again, deliberately |
| Misreported `409` | Every cancelled transaction claimed "that employee id already exists". Fixed by reading `CancellationReasons`; moot now that nothing transacts |

**Frontend:** run in a real browser (headless Chrome against `py -m http.server 8000`) and verified
against the live API. Re-run after the enums were removed: all six rows render, the department and
employment-type dropdowns build themselves from the loaded records, and the status filter orders
itself Pending → In Progress → Onboarded. Against a stub API returning zero employees, the form
degrades to text inputs rather than to empty dropdowns nobody can submit.

The store was exercised end to end through the real `App.store` — 23 assertions, all passing,
including the ones easy to get wrong:

- `DELETE` used to return 204 with an empty body, which `response.json()` would throw on — the
  204 branch in `request()` stays because that shape is still worth handling
- `getEmployee` resolves `null` on a 404 while `updateEmployee` and `archiveEmployee` reject on one
- a 400 arrives with a `fields` map whose keys match the form inputs exactly
- `id`, `employeeId` and `checklist` smuggled into an **update** body are stripped before sending;
  `employeeId` is sent on create only, because that is the one moment it can be set
- the client's `computeStatus` / `progress` agree with the server's on the same record

Rendering was verified from the returned DOM: six rows with employee numbers `E1001`–`E1006`, all
three status badges, progress bars at 100/63/75/25/0/0%, and a deep-link straight to
`#/employees/E1003/checklist` resolving correctly. The ids are readable and typeable now, which
is a smaller router test than the UUID one it replaces and a much better one for a person
holding a payroll export.

The failure path was forced by pointing the API hostname at a dead port. Result: the banner reads
"Could not reach the server", `#app` reads "Could not load employees", and — the check that matters
— **no employee renders at all**. Console output on failure is exactly one deliberate
`console.error`; no `Uncaught (in promise)`.

**Not automated.** There is no JS test runner in this project and adding npm was out of scope, so
none of the frontend checks run in CI. [phase3-testing.md](phase3-testing.md) is the repeatable
version.

---

## Auth: two roles, enforced server-side

Two accounts, two views. Officials get the console that was already here; the `employee` role gets a
read-only directory. What matters is where that split is enforced.

**The browser is not trusted with it.** `POST /login` verifies a password against a PBKDF2 hash and
returns an HS256 JWT carrying the role as a claim; a REQUEST authorizer verifies the signature on
every other route and passes the role down in `requestContext.authorizer`. The signing key is only
ever in two Lambdas' environments. So the role in `sessionStorage` decides which screen is drawn,
and the signature decides what the API does — an employee who edits `"role":"employee"` into
`"official"` reaches an officials screen where every request comes back 401 or 403. That is why the
route guard in `js/app.js` is allowed to be twenty lines with no cryptography in it.

Three decisions inside that worth recording:

**The JWT is hand-written, not PyJWT** (`src/common/tokens.py`). `src/requirements.txt` is empty
because boto3 ships in the Lambda runtime — no install step, no lockfile. One HMAC and two base64
calls are not worth ending that. The trade is that PyJWT is audited and this is not, so the verify
path is deliberately narrow: one algorithm, no `kid`, no JWK fetch, and `alg: none` rejected
explicitly. Every one of those is a feature this system does not need and a hole if it is wrong.

**The authorizer allows the whole API; the role check is in Python.** API Gateway will happily take
a method-scoped policy — "allow GET, deny POST" — which sounds tighter. It puts authorization in an
IAM document pytest cannot see, and authorizer results are cached against the token, so the cached
policy is reused for a request to a different method. So `require_official()` is one line at the top
of each writing handler instead, and `tests/test_roles.py` covers all four.

**Restricting a read constructs a new object rather than deleting keys.** `restrict_for_employee`
names the eight fields an employee may see. A `del` list would leak every field added to the model
from the moment it exists until somebody remembers; this way a new field is private until somebody
deliberately exposes it, and `tests/test_roles.py` asserts each hidden field individually so a
leak names itself.

**Two traps this hit, both worth knowing.** API Gateway generates the 401 itself when the authorizer
rejects a token, and a gateway-generated response does not inherit the CORS headers the Lambdas set
— so without the `GatewayResponses` block in `template.yaml`, a browser sees an opaque CORS error
instead of the 401 and an expired session looks like an outage. And the CORS preflight must stay
anonymous (`AddDefaultAuthorizerToCorsPreflight: false`): an `OPTIONS` request carries no
`Authorization` header by definition, so authorizing it 401s every preflight.

`caller_role()` returns `None` for a request with no identified caller, and `require_role()` turns
that into a 403. It used to default to the employee role, which *sounded* like failing closed and
was not: the employee role is not a closed door, it reads the whole staff directory. So a stack that
lost its `Auth` block would have served every name, department, job title, start date and onboarding
status to anyone who asked, with all tests still passing. That came out of the endpoint audit below,
and it is the one finding that was a real hole rather than a weakness.

### What the audit found

Thirty-five forged tokens got nowhere — rewritten role and `sub` claims, stripped and swapped
signatures, `alg: none`, duplicate JSON keys, missing `exp`. No bypass. What it did find was six
things around the edges, all now fixed:

| Finding | Fix |
|---|---|
| `caller_role` defaulted to a role that can read everything | Returns `None`; both readers call `require_role` |
| Signing key was a plaintext Lambda env var, readable via `lambda:GetFunctionConfiguration` | Generated into Secrets Manager, fetched per cold start, `GetSecretValue` granted to two functions |
| No rate limit on `/login`, amplified by `Allow-Origin: *` | Gateway throttle on `POST /login`; origin is now the `AllowedOrigin` parameter |
| No revocation path | Rotating the secret invalidates everything within the 60s authorizer cache |
| `api_arn()` returned `Resource: '*'` on an unparseable ARN | Raises; the request is refused |
| No `iss`/`aud`, so a dev token worked against prod on a shared key | Both claims minted and checked |

Two client-side ones came out of it too. The role is now read from the token's own payload rather
than a separate `role` field in `sessionStorage`, so editing that field does nothing — tampering
means tampering with the token, which the API answers with a 401. And the "your session expired"
message now arrives as an inline error on the login form: it used to be written to the error banner,
which the redirect's own `render()` then cleared, leaving the message alive only in the
screen-reader live region. Assistive tech was told what happened and nobody else was.

Two more came out of load-probing the deployed stack, and both are about the fact that Lambda
concurrency is an **account-wide pool** — this account's ceiling is 10 executions:

- **`/login` could starve the rest of the API.** It is the one route needing no credentials, so a
  burst against it consumed the whole pool and left the authenticated endpoints returning 500 for
  want of an execution slot — an unauthenticated denial of service against everything else, through
  the one door that has to stay unlocked. `LoginFunction` now reserves 2 of the 10, which caps it
  and guarantees it in the same move. The gateway throttle was also dropped to 5/s, so the API
  sheds load as a clean 429 instead of letting through more than Lambda will run.
- **Gateway 5XX responses carried no CORS headers.** Exactly the trap the 401 had: a throttled
  burst reached the browser as an opaque CORS failure rather than a 500. `DEFAULT_5XX` now carries
  them too.

**Still open, and known.** The throttle is stage-wide rather than per-IP (WAF is a cost decision).
Revocation is all-or-nothing — there is no per-user sign-out, because there are no per-user records.
`employee` remains an account rather than a person. And 10 concurrent executions is low enough that
ordinary use can hit it; the fix for that is a service-quota increase, not code.

---

## Not built yet

- **Document upload** (offer letter, ID proof) — a visible stub sits on the checklist page marking
  where it goes; the storage itself is S3 in a later phase.
- **Who wrote a comment, and when** — the item stores `updatedAt`, and there is now an authenticated
  username to record against it (`requestContext.authorizer.username`), but neither handler writes
  it and the API does not return either. Cheap to add now that the caller has a name.
- **Per-employee scoping** — the `employee` role sees the whole directory, not just their own
  record. There is no link between a login and an employee number: `employee` is an account, not a
  person. Giving each hire their own view means real user records, which is Cognito, not two
  hard-coded accounts.
- **Real credential storage** — the signing key now lives in Secrets Manager, but the two accounts
  are still PBKDF2 hashes in `src/common/accounts.py` and the demo passwords are in a public README.
  A Cognito user pool is where this goes. Don't put real employee data in the dev stack.
- **Per-IP login rate limiting** — `POST /login` is throttled stage-wide at the gateway, which caps
  the bill and the guessing rate but cannot tell one caller from another. Per-IP is a WAF rate-based
  rule, which is a cost decision rather than a code one.
- **Per-user session revocation** — rotating the signing key invalidates every session at once.
  Revoking one person's needs per-user records, which is the Cognito item above.
- **Per-item permissions** — the `owner` field on checklist items is still display-only. Officials
  can tick IT's items, which is decision 2 below and unchanged; the role split is between officials
  and employees, not within officials.
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
| 3. Are employees hard-deleted or deactivated? | ~~Hard-deleted, whole partition at once~~ → **reversed after Phase 4**: archived in place, see below |
| 4. Should the list default to in-progress hires only? | No — returns everyone |
| 5. Does status need a manual override? | No — still derived from the checklist |

Number 5 is the one to watch. Reversing it turns `status` from a derived value into a stored field
that can drift out of sync with the checklist — which the current design makes impossible.

### Number 3, reversed: archiving instead of deleting

`DELETE /employees/{id}` no longer removes anything. It stamps `archivedAs` on the profile —
`Onboarded` if the checklist was complete, `Onboarding Cancelled` if it wasn't — and the employee
drops out of `GET /employees`. The reasoning, and the three decisions that came with it:

**Why reverse it.** A deleted row answers no questions. HR needs to know who was hired, how far
their onboarding got, and what the notes on it said; "we deleted it" is the wrong answer to an
audit. The old hard delete also threw away the comments, which are the most useful part of the
record when onboarding stalls.

**`archivedAs` is stored, and that is not a contradiction of number 5.** `status` is derived because
it describes the checklist as it stands right now. `archivedAs` records a decision a person made at
a point in time, and there is nothing in the table to recompute it from. Derive it and a cancelled
record silently promotes itself to a completed one the moment someone ticks a leftover box. The two
coexist on the wire: an archived employee can read `"status": "In Progress"` and
`"archivedAs": "Onboarding Cancelled"` at once, and both are true.

**The email is not reserved.** The archived record still shows that address as theirs, but nothing
stops a new hire taking it — the guard item that used to reserve it went when the table collapsed to
one item per employee. Re-hiring under the same work email now produces a second record sharing one
mailbox, silently.

**The record freezes.** `PUT` and `PATCH` return `409`. Without this, a cancelled record could be
ticked to 8 of 8 and left sitting there stamped `Onboarding Cancelled` — two claims in one record
with nothing to say which is the lie. The tick path enforces it with
`attribute_not_exists(archivedAs)` in the same `ConditionExpression` as the update — the stamp and
the checklist are attributes of one item now — so the window between checking and writing does not
exist.

**What is deliberately missing.** There is no un-archive endpoint and no archived view in the UI.
Reversing an archive means clearing `archivedAs` on the item directly. That is a decision to
revisit the first time someone actually needs it, rather than three endpoints built on a guess.
