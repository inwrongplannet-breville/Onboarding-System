# Database design

Everything about how employees are stored in DynamoDB, and why. This is the authoritative
description of the data model — [design.md](design.md) summarises it and links here.

- [Two tables, not one](#two-tables-not-one)
- [Attendance table](#attendance-table)
- [Promotion](#promotion)
- [The table](#the-table)
- [The item](#the-item)
- [Access patterns](#access-patterns)
- [How ticking a box stays safe](#how-ticking-a-box-stays-safe)
- [Rules that must not be broken](#rules-that-must-not-be-broken)
- [What this design gives up](#what-this-design-gives-up)
- [Cost and size](#cost-and-size)
- [Deploying a schema change](#deploying-a-schema-change)
- [Verified behaviour](#verified-behaviour)

---

## Two tables, not one

The employee lifecycle started as one table, `OnboardingTable`, holding every employee regardless
of where they stood in onboarding. It now uses two profile tables, split by lifecycle stage rather
than by subtype. Attendance is a third, independent time-series table described below.

| Table | Backs | Holds |
|---|---|---|
| `OnboardingTable` | `#/onboarding` | People whose onboarding is not yet finished |
| `EmployeeTable` | `#/tracking` and `#/interns` | Onboarded staff — employees **and** interns, side by side |

`OnboardingTable`'s own schema and item shape are **unchanged** by the split — see
[The table](#the-table) and [The item](#the-item) below, both still describe it exactly. What
changed is that a record now *leaves* it, via [promotion](#promotion), rather than staying there
for the rest of its life.

An employee and an intern were briefly two tables, `EmployeeTable` and `InternTable`, split by
subtype - each with its own key attribute and prefix. They were merged before the first deploy:
`entityType` was already the discriminator the DynamoDB item-type convention calls for (it has
distinguished item kinds since before this table held more than one), a second table keyed on its
own `internKey`/`INT#` prefix was redundant with it, and `attribute_not_exists(employeeKey)` on one
shared table now makes **"promoted as both an employee and an intern" structurally impossible** -
two independent tables' conditions never guaranteed that on their own.

### `EmployeeTable`

| | |
|---|---|
| Partition key | `employeeKey` (String), `EMP#<employeeId>` — same attribute and prefix as `OnboardingTable`, so a promoted record keeps the id it always had, whichever kind it becomes |
| Sort key | none |
| Secondary index | `ByReportingManager` — GSI, HASH `reportingManagerId`, `Projection: ALL` |
| Billing | `PAY_PER_REQUEST` |

Every `PROFILE_FIELDS` attribute `OnboardingTable` has, minus `checklist`/`archivedAs`/`archivedAt`
(archiving is an onboarding-table concept — a staff record can never be archived), plus:

| Attribute | On | Notes |
|---|---|---|
| `entityType` | both | `"Employee"` or `"Intern"` — the one attribute that tells the two kinds of item apart. `common/models.py`'s `is_employee_item()`/`is_intern_item()` are the only way handlers read it |
| `onboardingChecklist` | both | the whole 8-item checklist, copied verbatim at promotion — frozen history, not something the checklist PATCH can reach (see the rename note below) |
| `onboardedAt` | both | ISO8601 `…Z`, when the move happened — the 7-day undo window (see [Promotion](#promotion)) is measured from this |
| `joinedOn` | both | date of joining, copied from `startDate` (which stays too) |
| `interns` | employee only | **sparse** — a list of intern ids, present only on a manager with one or more, absent everywhere else, including on every intern item. Written by `POST /staff/employees/{id}/interns`, `REMOVE`d by `DELETE .../interns/{internId}` the moment it would otherwise go empty. Absent and `[]` mean the same thing to a reader — the same rule this file already applies to `archivedAs` and to a checklist comment |
| `reportingManagerId` | intern only | required, must name an `EmployeeTable` item whose own `entityType` is `"Employee"` — never present on an employee item |

`Projection: ALL` on the GSI because the interns dashboard renders the whole record — a keys-only
projection would turn one `Query` into a `Query` plus N `GetItem`s. The index is **sparse**: only
intern items carry `reportingManagerId` at all, so an employee item is simply absent from it rather
than something a query has to filter out.

### The type guards this table needs that two tables did not

Once one table holds both kinds of record, "this id resolves to a row in `EmployeeTable`" stops
meaning "this id is an employee" — every handler that used to be able to assume that now checks
`entityType` explicitly:

| Handler | Guard | Why |
|---|---|---|
| `promote_to_intern.py` | new manager must be `is_employee_item` | otherwise an intern could be named as another intern's manager |
| `set_intern_manager.py` | same, for a reassignment's new manager; and the reassignment target itself must be `is_intern_item` | same reason, both directions |
| `add_manager_intern.py` | linked record must be `is_intern_item` | otherwise an employee could be linked into someone's `interns` list as if they were an intern |
| `delete_staff_employee.py` | target must be `is_employee_item` | so this route cannot delete an intern's record |
| `delete_staff_intern.py` | target must be `is_intern_item` | and vice versa |

This is the one real cost of merging the tables, and it is a cost paid in explicit checks rather
than in structural safety: nothing here was silently lost, but nothing is free either.

## Attendance table

`AttendanceTable` stores at most one declaration per employee per business date:

| | |
|---|---|
| Partition key | `employeeKey` (String), `EMP#<employeeId>` |
| Sort key | `attendanceDate` (String), `YYYY-MM-DD` |
| Secondary index | `AttendanceByMonth`: HASH `attendanceMonth`, RANGE `dateEmployeeKey`, projection `ALL` |
| Billing | `PAY_PER_REQUEST` |

Each item stores `employeeId`, snapshot fields `employeeName`, `employeeRole` and `department`, the
date/month/index keys, `status`, optional `note`, `markedAt`, `updatedAt`, `updatedBy`, and
`updatedByRole`. `employeeRole` is copied from the profile's `jobTitle`; it is not the authentication
role. Identity fields are populated server-side.

The base key supports one employee's monthly Query. `AttendanceByMonth` supports one Query for HR's
monthly sheet. The write is naturally idempotent because a second declaration for the same
employee/date replaces the same key.

Missing items are meaningful: for an applicable elapsed date they mean absent. They are filled into
the API report, never materialised by a nightly job. Today remains upcoming until 08:30
Asia/Kolkata, becomes provisionally absent when the window opens, and employee writes close at
18:00. Future dates remain blank. Officials can write any employee/date at any time.

The attendance roster combines non-archived `OnboardingTable` records with Employee and Intern rows
from `EmployeeTable`, then collapses duplicates by immutable employee ID. This keeps attendance
available during onboarding and during a safely interrupted promotion sequence.

## Promotion

"Move to main employee dashboard" / "Move to intern dashboard" and their reverse are each a short
**sequence of small, independently callable endpoints** — not a single `TransactWriteItems`, even
though DynamoDB transactions do span multiple tables in one region and this codebase has held that
exact machinery before (`git show da9ecc3^:src/common/db.py`). The individual-endpoint shape is a
deliberate choice, not an oversight: every step is independently triggerable and observable from
the client, matching how HR actually drives the UI one click at a time.

That choice has a cost, and it is worth being plain about it: a closed tab, a lost connection or a
500 partway through a sequence leaves the data in a state no server-side process will ever finish
or reverse. The mitigation is a rule every sequence below obeys:

> **The destructive step is always last, and every endpoint is idempotent.**
> An interrupted sequence leaves a *duplicate* — someone visible on two dashboards, or a manager
> link recorded twice — never a vanished record. Re-running the whole sequence from step 1 is
> always safe and always finishes the job.

Two guards make that a property of the API, not just of a well-behaved client:

- `DELETE /onboarding/{id}` refuses with 409 unless the record already exists in `EmployeeTable`,
  as an employee or as an intern. Called out of order, it cannot destroy anything.
- `DELETE /staff/employees/{id}` and `DELETE /staff/interns/{id}` refuse unless the onboarding row
  already exists again (`POST /onboarding/restore` has run) **and** the promotion is still inside
  the seven-day undo window. Without this pair, either route is an unrestricted "delete any staff
  record" endpoint with no copy anywhere first.

### The four sequences

**Promote a non-intern** — `POST /staff/employees` (body `{employeeId}`) → `DELETE /onboarding/{id}`.
The first call is gated on `derive_status(checklist) == 'Onboarded'`: the whole premise is that
`OnboardingTable` holds unfinished onboarding and `EmployeeTable` holds finished onboarding, and an
ungated promote would put a half-ticked checklist somewhere with no route left to finish it — the
checklist PATCH is onboarding-only. Interrupted after step 1: visible on both `#/onboarding` and
`#/tracking`; re-running step 1 is a harmless 409, step 2 then completes.

**Promote an intern** — `POST /staff/interns` (body `{employeeId, reportingManagerId}`) →
`POST /staff/employees/{managerId}/interns` (body `{internId}`) → `DELETE /onboarding/{id}`. The
first call validates `reportingManagerId` names a live `EmployeeTable` item **whose own entityType
is `Employee`**, not merely a live item — an onboarding record's free-text `manager` string is not
enough to establish that on its own, and neither, now, is existence alone: the manager and the
intern share one table, so an intern's id resolves in `EmployeeTable` too. Interrupted after step
1: on `#/onboarding` and `#/interns`, but the manager's `interns` list does not yet name them.
Interrupted after step 2: on both dashboards, link correct. Re-run from step 1 either way — it
409s harmlessly if already done.

**Undo a move, within seven days** — `POST /onboarding/restore` (body `{employeeId}`) →
(intern only) `DELETE /staff/employees/{managerId}/interns/{id}` →
`DELETE /staff/employees/{id}` or `DELETE /staff/interns/{id}`. The window is checked twice — once
in the restore call, once again in the final delete — because a call arriving out of order must
not slip through on a stale assumption that the window was open when an earlier step ran. Past the
window there is no other way back: a mistaken promotion older than a week needs table access, the
same trade a mistaken hard delete always makes.

**Reassign an intern's manager** — `PUT /staff/interns/{id}/manager` (body `{reportingManagerId}`,
returns the *previous* manager id) → `POST /staff/employees/{newManagerId}/interns` →
`DELETE /staff/employees/{oldManagerId}/interns/{id}`. New link added before the old one is
removed, on purpose: if the last step never runs, the intern is reachable from both managers'
`interns` lists — a harmless duplicate. The reverse order risks the opposite, an intern linked to
nobody, which this whole design exists to avoid. `GET /staff/interns?managerId=` reads the
`ByReportingManager` GSI directly, so the *dashboard* is correct the moment step 1 lands even if
steps 2–3 have not yet run — only the denormalised `interns` list can be briefly stale.

### Known gap: no server-side compensation

There is no sweep for a record left in an inconsistent state (present but with a stale `interns`
link, say), no idempotency key tying one sequence's steps together, and no automatic retry. What
this design guarantees today is that every interrupted state is *visible on the dashboards* and
*fixable by re-running the sequence* — not that it cannot happen. Building a compensation/rollback
system is explicitly deferred; see
[docs/api.md](api.md#known-gap-no-automatic-compensation) for the endpoint-level version of this
note.

### Why `dynamodb:DeleteItem` exists now

[Rules that must not be broken](#rules-that-must-not-be-broken) and
`src/handlers/delete_employee.py` both make a point of no function anywhere holding
`dynamodb:DeleteItem` — deleting an employee has always meant archiving in place. Promotion breaks
that: `DELETE /onboarding/{id}`, `DELETE /staff/employees/{id}` and `DELETE /staff/interns/{id}` are
real deletes, because the whole point of the promotion design is that a record's *table* is the
fact of where it stands, and a promoted record cannot stay in `OnboardingTable` forever.

It is scoped as narrowly as it can be. `DeleteOnboardingRecordFunction` holds it on `OnboardingTable`
only; `DeleteStaffEmployeeFunction` and `DeleteStaffInternFunction` each hold it on `EmployeeTable`
only, and neither can also `PutItem` there, so a bug in either cannot turn a delete into data loss
beyond the one table it is scoped to. What is genuinely lost, relative to the archive-in-place
`DELETE /employees/{id}`, is recoverability past the seven-day undo window — after that, a mistaken
promotion needs table access, exactly the trade a mistaken hard delete has always made.

## The table

One table. **One item per employee, and employees are the only kind of item in it.**

| | |
|---|---|
| Partition key | `employeeKey` (String) |
| Sort key | none |
| Secondary indexes | none |
| Billing | `PAY_PER_REQUEST` |
| Point-in-time recovery | off |
| `DeletionPolicy` / `UpdateReplacePolicy` | `Delete` |
| Table name | **generated by CloudFormation** — see [Deploying a schema change](#deploying-a-schema-change) |

Declared as `AWS::DynamoDB::Table` rather than `AWS::Serverless::SimpleTable`, even though a lone
partition key would now fit SimpleTable. SimpleTable exposes only `PrimaryKey`,
`ProvisionedThroughput`, `SSESpecification`, `TableName` and `Tags`: it cannot declare
`PointInTimeRecoverySpecification` or `BillingMode`, and it hides `KeySchema`, which in this repo is
documentation.

Key strings are built in exactly one place, `src/common/keys.py` — `pk()` and
`employee_id_from_pk()`, alongside `key()` and the `KEY_ATTRIBUTE` /
`EXISTS` / `NOT_EXISTS` constants the handlers build their `Key=` arguments and condition
expressions from. Nothing else names the attribute or concatenates the `EMP#` prefix.

## The partition key is the employee number

`EMP#E1024`, not `EMP#<uuid>`. The id is the employee number HR types on the create form, and that
choice is what turns the create-time `NOT_EXISTS` condition from a formality into the only
uniqueness constraint the table has.

DynamoDB can enforce uniqueness on a partition key and on nothing else. While the key was a UUID
there was nothing meaningful to enforce — the condition guarded against a collision no one would
ever see. With the employee number in the key, a second `POST` under an existing number is refused
by DynamoDB itself, in the same write, with no read-then-write race to lose.

What it costs:

- **The id is immutable.** DynamoDB cannot move an item between partition keys; an `UpdateItem`
  naming a different key does not rename anything, it upserts a second employee and leaves the
  first one sitting there. So `employeeId` is absent from `EDITABLE_FIELDS`, and correcting a
  mistyped number means archiving that record and creating the right one.
- **Case has to be normalised.** `e1024` and `E1024` are two different partition keys and therefore
  two different employees, which is precisely the failure the key was chosen to prevent. Every id
  is upper-cased on the way in — `clean_employee_id()` in `common/models.py` — both from the create
  body and from the `{id}` path parameter, so a URL typed in the wrong case still resolves.
- **The format is constrained.** `^[A-Z0-9][A-Z0-9-]{1,19}$`: 2–20 characters, letters, digits and
  hyphens. Tighter than a key strictly needs, because this string is concatenated into the key —
  `#` would let a caller forge a key in another namespace and whitespace would produce two ids that
  are indistinguishable in the console.
- **Archived numbers stay taken.** Archiving leaves the item in the table, so the key is still
  occupied and re-hiring requires a new number. That is the honest outcome; reusing a number would
  attach a second person's history to the first person's id.

The seed fixtures use `E1001`–`E1030`, so a reseed lands the same people on the same ids and a
hand-written link like `#/onboarding/E1023` survives a table reset.

## The item

This is an illustrative item shape, not the identity of the current `E1024` seed fixture.

```
employeeKey = EMP#<employeeId>

{
  "employeeKey":    "EMP#E1024",
  "entityType":     "Employee",
  "employeeId":     "E1024",

  "firstName":      "Priya",
  "lastName":       "Sharma",
  "email":          "priya.sharma@breville.com",
  "phone":          "+61 412 883 016",
  "department":     "Engineering",      // Engineering | HR | Finance | Operations
  "jobTitle":       "Software Engineer",
  "manager":        "Santosh Kumar",
  "startDate":      "2026-07-06",
  "employmentType": "Full-time",        // Full-time | Contract | Intern

  "personalEmail":  "priya@example.com", // the employee's own; absent until they fill it in
  "address":        "12 Smith Street",   // the employee's own; absent until they fill it in

  "createdAt":      "2026-08-24T14:12:07Z",
  "updatedAt":      "2026-08-24T14:12:09Z",

  "archivedAs":     "Onboarding Cancelled",   // absent while active
  "archivedAt":     "2026-08-24T14:20:00Z",   // absent while active

  // Note what is NOT here: documents. There is no `documents` attribute, and that
  // is deliberate rather than pending - see the section below.
  "checklist": [                              // 8 entries, in CHECKLIST_TEMPLATE order
    {
      "itemId":    "offer-letter",
      "label":     "Offer letter signed",
      "owner":     "HR",
      "order":     1,
      "done":      true,
      "comment":   "chased payroll twice",    // absent when there is no note
      "updatedAt": "2026-08-24T14:12:07Z"
    },
    ...
  ]
}
```

### What is stored and what is derived

`status` and `progress` are **never stored**. They are computed on every read by
`derive_status()` and `progress()` in `src/common/models.py`, and computed nowhere else — so they
cannot drift from the checklist they describe. The frontend renders what the API returns and derives
nothing of its own.

`archivedAs` is the deliberate exception: it records a decision a person made at a point in time,
and there is nothing in the table to recompute it from. Deriving it would let a cancelled record
silently promote itself to `Onboarded` the moment someone ticked a leftover box.

### Two fields that look redundant and are

- **`order`** duplicates the list position, and nothing reads it. It stays because a raw item in the
  console is far easier to read with it than without. `test_models.py` pins it to the position so it
  cannot drift into a lie.
- **`entityType`** is a constant now that employees are the only item type. It is made load-bearing
  by the `Scan` filter in `list_employees.py`, so anything else that ever lands in this table is
  excluded from the list without another visit to that file.

### An absent attribute and an empty string mean the same thing

`comment` is `REMOVE`d when cleared, not stored as `""`. Both read back as `''` through
`to_api_checklist_item()`, so making a client distinguish them buys nothing — and `REMOVE` keeps the
item smaller and the intent legible in the console. Same for `archivedAs`/`archivedAt`, which are
absent on an active employee and surface as `''`.

## Access patterns

Six routes, six DynamoDB shapes. Note there is no `Query` anywhere — with one item per employee
there is no partition to query.

| Route | Operation | Consistency |
|---|---|---|
| `GET /employees/{id}` | `GetItem` | eventual |
| `GET /employees` | `Scan`, paginated on `LastEvaluatedKey`, `FilterExpression` on `entityType` | eventual |
| `POST /employees` | `PutItem` with `attribute_not_exists(employeeKey)` | — |
| `PUT /employees/{id}` | `UpdateItem` + consistent `GetItem` for the response | **strong** on the re-read |
| `PATCH .../checklist/{itemId}` | `UpdateItem` on nested paths + consistent `GetItem` | **strong** on the re-read |
| `PATCH /employees/{id}/contact` | `UpdateItem` (`SET`/`REMOVE` on up to 3 attributes) + consistent `GetItem` | **strong** on the re-read |
| `GET /employees/{id}/documents` | **nothing** — S3 only | — |
| `POST /employees/{id}/documents/{slot}` | consistent `GetItem` (exists? archived?) | **strong** |
| `DELETE /employees/{id}` | consistent `GetItem` → `UpdateItem` → consistent `GetItem` | **strong** |

**Why `PUT` and `PATCH` read consistently and `GET` does not.** Reads are eventually consistent by
default, so a handler that writes and then re-reads to build its response can legitimately be handed
the pre-write values. The UI repaints from that response, so a stale read shows the user a checkbox
snapping back to where they just moved it from. `GET` pays no such price — nothing it returns was
written a millisecond earlier by the same caller — and a strong read costs twice as much. The flag
lives on `load_employee()` in `src/common/repository.py`.

**Why `DELETE` reads consistently.** The archive stamp is computed from that read. A checklist one
tick behind is the whole difference between `Onboarded` and `Onboarding Cancelled` — a distinction
nobody would ever think to go back and check.

**Why `Scan` for the list, and why it is correct.** `Query` needs a partition key, and employees are
spread across every partition by design. The usual counter-suggestion is a sparse GSI with a constant
partition key so the list becomes a `Query`; that is strictly worse here, because the list view
filters by derived status and draws a progress bar, both of which need the checklist. A
profile-only index would give you N items and then N follow-up reads. One `Scan` already returns
everything. It also reads archived employees and drops them in Python, because no index would let it
skip them — the price of soft deletion on a `Scan`-based list.

**No transactions anywhere.** An employee is one item, so there is nothing to keep atomic across
items. `src/common/db.py` holds a single resource-level client; the low-level client,
`TypeSerializer` and the `CancellationReasons` demux in `handler.py` were all retired with the
multi-item design. Promotion is a genuine multi-item, multi-table write and still does not bring
any of that back — see [Promotion](#promotion) for why it is a sequence of small endpoints
instead.

### IAM, per function

Written out inline rather than via SAM policy templates, which are coarser than they look:
`DynamoDBReadPolicy` grants `Scan` to handlers that only need `GetItem`, and `DynamoDBWritePolicy`
grants no read actions at all, which would push the delete handler to full CRUD.

| Function | Table(s) | Actions |
|---|---|---|
| `ListEmployeesFunction` | Onboarding | `Scan` |
| `CreateEmployeeFunction` | Onboarding | `PutItem` |
| `UpdateEmployeeFunction` | Onboarding | `UpdateItem`, `GetItem` |
| `DeleteEmployeeFunction` | Onboarding | `GetItem`, `UpdateItem` — **no `DeleteItem`** |
| `SetChecklistItemFunction` | Onboarding | `UpdateItem`, `GetItem` |
| `GetEmployeeFunction` | both | `GetItem` on each — `find_record()` tries them in turn |
| `UpdateOwnContactFunction` | both | `UpdateItem`, `GetItem` on each — writes whichever table the caller's own record is in |
| `RequestDocumentUploadFunction` | both | `GetItem` on each |
| `GetDocumentsFunction` | none | S3 only |
| `PromoteToEmployeeFunction` | Onboarding, Employee | `GetItem` (Onboarding), `PutItem` (Employee) |
| `PromoteToInternFunction` | Onboarding, Employee | `GetItem` (Onboarding), `GetItem`+`PutItem` (Employee — GetItem to verify the manager is an employee, PutItem for the new intern row) |
| `AddManagerInternFunction` | Employee | `GetItem`+`UpdateItem` — one grant, since the manager and the linked intern are the same table now |
| `RemoveManagerInternFunction` | Employee | `GetItem`, `UpdateItem` |
| `SetInternManagerFunction` | Employee | `GetItem`+`UpdateItem` — covers the intern's own row and the new manager's, same table |
| `DeleteOnboardingRecordFunction` | Onboarding, Employee | `GetItem` (Employee, the ordering guard — matches either kind), `DeleteItem` (Onboarding only) |
| `RestoreOnboardingFunction` | Onboarding, Employee | `GetItem` (Employee), `PutItem` (Onboarding) |
| `DeleteStaffEmployeeFunction` | Onboarding, Employee | `GetItem` (Onboarding, the restore guard), `GetItem`+`DeleteItem` (Employee only) |
| `DeleteStaffInternFunction` | Onboarding, Employee | Same shape as above, against the same table — `is_intern_item` in the handler is what tells the two functions' targets apart |
| `ListStaffEmployeesFunction` | Employee | `Scan` |
| `ListInternsFunction` | Employee | `Scan` (same table `ListStaffEmployeesFunction` scans), plus `Query` on the `ByReportingManager` index ARN |

`DeleteEmployeeFunction` having no `DeleteItem` is the point, and it matters more now than it did:
one stray `DeleteItem` would take an employee's checklist and every note on it in a single call.

## How ticking a box stays safe

The checklist used to be eight sibling rows addressed by sort key, chosen specifically so that
ticking a box needed **no read-modify-write of a list**. Embedding the list could have thrown that
away. It does not.

An entry is addressed by its **index in a nested document path**, and DynamoDB applies the mutation
server-side. Nothing reads the list into Python and writes it back:

```
i     = CHECKLIST_INDEX[item_id]              # static, from CHECKLIST_TEMPLATE order
entry = '#checklist[{}]'.format(i)

UpdateExpression:     SET <entry>.#done = :done,
                          <entry>.#updatedAt = :updatedAt,
                          #updatedAt = :updatedAt
                      [REMOVE <entry>.#comment]

ConditionExpression:  attribute_exists(employeeKey)
                      AND attribute_not_exists(#archivedAs)
                      AND <entry>.#itemId = :itemId
```

DynamoDB serialises writes to a single item and applies each expression against the **latest
committed value**, not against a snapshot the client read. So a `{"done": true}` PATCH and a
`{"comment": "..."}` PATCH cannot clobber each other **even when they name the same entry** — the
second applies on top of the first. That is a stronger guarantee than "different rows, so no
conflict", reached by a different mechanism.

Three consequences worth knowing:

- **The archive freeze is now one term in this write's own condition**, not a `ConditionCheck`
  against a sibling row inside a transaction. Same guarantee, one round trip, no transaction.
- **The top-level `updatedAt` and the entry's `updatedAt` are disjoint paths**, so both are set from
  one `:updatedAt` value with one placeholder.
- **A failed condition is read back, not guessed.** `load_archive_state()` distinguishes three
  outcomes: no such employee → `404`; archived → `409`; **exists and active** → the stored list has
  drifted from the template, which is a corrupted record rather than a caller error, so it raises and
  becomes a `500` with a trace in CloudWatch. Reporting that as a `404` would hide it forever.

An unknown `itemId` never reaches the write at all — `VALID_ITEM_IDS` rejects it up front, which is
now the only thing validating an item id, since there is no sibling row whose absence would.

## Rules that must not be broken

`CHECKLIST_INDEX` (`src/common/checklist_template.py`) is **positional**. Two rules follow, and
neither is defensive decoration:

**1. Every write pairs its path with `checklist[i].itemId = :itemId`.** A `SET` on an out-of-range
list index **appends to the end of the list** rather than failing. Without this condition, a desynced
list would silently grow a ninth entry instead of erroring. When the path does not resolve the
comparison evaluates to *false*, not an error, so the write is rejected cleanly.

`tests/test_handlers.py::test_a_tick_on_a_drifted_checklist_fails_instead_of_landing_on_the_wrong_item`
deletes an element out from under the index and asserts the writes fail and nothing appended.

**2. No code may ever `REMOVE checklist[i]` or `list_append` a whole element.** Removing an element
shifts every later index down by one, permanently desynchronising `CHECKLIST_INDEX` — and no
condition would catch it. Only `checklist[i].comment` is ever removed.

Two smaller traps, both found the hard way:

- **`boto3.dynamodb.conditions.Attr` cannot express these paths.** `Attr` splits on `.` for nesting
  but does not parse `[n]`, so `Attr('checklist[0].itemId')` emits a placeholder for the literal
  attribute name `checklist[0]`. Use a plain string `ConditionExpression`.
- **A projection naming only absent attributes returns no `Item` at all.** An active employee has no
  `archivedAs`, so `load_archive_state()` projects `employeeId, #archivedAs` — one always-present
  attribute alongside it. Project `archivedAs` alone and the function reports "no such employee" for
  someone who plainly exists.

Also: `comment`, `order`, `owner` and `status` are DynamoDB reserved words, so map keys use `#name`
placeholders throughout rather than selectively.

## What this design gives up

**Work email uniqueness is not enforced. Two employees may hold the same address.**

There used to be a second item, `employeeKey = EMAIL#<lowercased address>`, written in the same
transaction as the profile with `attribute_not_exists(employeeKey)`. DynamoDB can only enforce uniqueness on a partition key,
and the partition key here is the employee number — so a guard item was the only way to hold the
constraint, and "one item per employee, nothing else" removes it by definition.

Putting a meaningful id in the key bought a real guarantee about **employee numbers** and none at all
about **mailboxes**. The two are not substitutes, and it is worth being explicit that the key change
did not quietly restore this one.

Consequences, all deliberate:

- `POST /employees` and `PUT /employees/{id}` **no longer return `409` for a duplicate address**, and
  `fields.email` has left the error contract.
- Archiving an employee no longer reserves their address. Re-hiring under it returns `201` and
  produces a second record on one mailbox, silently.
- Restoring the constraint means reintroducing a sort key, which is a table replacement.

`tests/test_handlers.py::test_two_employees_may_now_share_a_work_email` documents the loss as a test,
so it reads as a decision rather than a regression. If that test ever starts failing, someone
reintroduced the guard and should say so loudly.

**Labels are snapshotted, not referenced.** Each entry stores its own `label`, `owner` and `order`,
so rewording an item in `CHECKLIST_TEMPLATE` does not reach existing employees without a backfill.
The upside is that a record shows the wording that was in force when the person was hired. This was
the shape the old `CHK#` rows had too, so it is not a regression — just not a free fix.

**Promotion has no compensation, and no manual reassignment past the intern flow.** See
[Promotion](#promotion) for the sequencing trade in full. Two smaller gaps worth naming on their
own:

- The free-text `manager` field on an onboarding record and `reportingManagerId` on a promoted
  intern are unrelated. Nothing checks the two agree, and nothing migrates one into the other —
  HR picks the reporting manager explicitly at promotion time.
- The `interns` list on `EmployeeTable` is a denormalisation of what the `ByReportingManager` GSI
  already knows. Every read that matters (`GET /staff/interns?managerId=`) goes through the index,
  not the list, so a stale list can never produce a wrong dashboard — but it can sit briefly out of
  sync with the index during a reassignment, between `PUT .../manager` and the two link calls that
  follow it.

## Cost and size

Measured against the deployed dev table, not estimated:

| | Before (9 items/employee) | After (1 item/employee) |
|---|---|---|
| Per employee | ~1,758 bytes across 9 items | **~1,200 bytes in 1 item** |
| Historical 6-employee measurement | 54 items / 12,461 bytes | **6 items / ~7,214 bytes** |
| Fraction of the 400 KB item limit | — | ~0.3%, or ~1.3% with all 8 comments at their 500-char cap |

The 32%-per-employee saving is entirely overhead, not data: eight copies of the partition key
(336 bytes), `SK` (129) and `entityType` (184) came to **649 bytes, 37% of every old
partition**.

Read and write costs move in opposite directions, and both are noise at this scale:

- **Reads get cheaper.** An employee is one `GetItem` of ~1.2 KB instead of a `Query` over 9 items,
  and a 1 MB `Scan` page now holds ~900 employees rather than ~370.
- **A tick gets more expensive.** WCU is charged on the whole item, so a checkbox costs
  `ceil(1.2 KB / 1 KB)` = 2 WCU instead of 1 for a small `CHK#` row. Offset by dropping the 2×
  transactional multiplier and the `ConditionCheck` — roughly a wash, or cheaper.
- **Contention concentrates.** All eight ticks now serialise on one item instead of eight. Irrelevant
  against DynamoDB's per-item write throughput, and correctness is unaffected for the reasons above.

Wrong for a million employees — at which point you denormalise a `doneCount` onto the item and add
the sparse GSI. The rule underneath: **`Query` when you know the partition key, `Scan` only when you
genuinely need every item.**

## Deploying a schema change

This is not hypothetical — renaming the partition key attribute from `PK` to `employeeKey` was one,
and it deployed as an ordinary `sam deploy` precisely because of what follows.

**A key-schema change is a table replacement, and an explicit `TableName` makes it undeployable.**
CloudFormation replaces a resource by creating the new one *before* deleting the old, which cannot
work when the name is taken. AWS documents this for `TableName` directly: specify a name and you
cannot perform updates that require replacement.

That is why this table has **no `TableName`** — CloudFormation generates it, and the next schema
change is an ordinary `sam deploy`. Nothing reads the name as a literal: Lambdas receive it through
the `TABLE_NAME` environment variable, and `scripts/seed_employees.py` resolves it from the stack's
`TableName` output.

Two other things that bite:

- Dropping a key attribute **requires** dropping it from `AttributeDefinitions` in the same change.
  An attribute definition that no key or index refers to is a validation error, not a warning.
- `UpdateReplacePolicy: Delete` means the old table and its data are deleted on replacement, not
  orphaned. That is wanted here; on a table with real data it is the opposite of wanted.

### Resetting the dev tables

```bash
py scripts/seed_employees.py --wipe --seed --yes
```

`--wipe` clears all three tables, resolving each name from the stack's `OnboardingTableName`,
`EmployeeTableName` and `AttendanceTableName` outputs (or their matching `--table-*` flags). One
pass over `EmployeeTable` clears employees and interns together; the attendance pass includes both
its partition and sort keys. Seeding drives the public
REST API, promote sequence included, so a broken seed is a broken API rather than a mystery.
Wiping cannot go through the API: `DELETE /employees/{id}` archives rather than erases, and none
of the promote endpoints hard-delete without a copy existing first, so wiping over HTTP would
leave records in place. `--wipe` goes straight at each table with `Scan` + `BatchWriteItem`.

That asymmetry is the design working: the API has no unconditional hard delete because history
should not be destroyable over HTTP by accident. Resetting the dev tables is a deliberate act
against the tables themselves.

## Verified behaviour

`pytest` covers the handlers and shared modules against in-memory DynamoDB (moto) — 442 tests.
moto emulates the API, not IAM, so the checks below were run against the deployed dev stack.

**Schema.** `AttributeDefinitions` and `KeySchema` each contain `employeeKey` alone. No `SK` attribute exists
on any item. All items are `EMP#` / `entityType: Employee`, each with an 8-entry `checklist` in
`CHECKLIST_TEMPLATE` order.

**Behaviour, confirmed live:**

| Check | Result |
|---|---|
| Write a comment, then tick the same item | both survive; `progress` recomputed |
| Tick an item, then write a comment on it | both survive |
| `PUT` a full profile edit | all 8 entries and their comments intact |
| Clear a comment | reads back `''`, `done` untouched, key removed from the entry |
| `PATCH` an unknown `itemId` | `404` |
| `POST` a duplicate work email | **`201`** — was `409` before this design |
| `PATCH` an archived employee | `409` |
| `PUT` an archived employee | `409` |
| `PATCH .../contact` an archived employee | `409` |
| `PATCH .../contact` naming HR-owned fields | those keys dropped; the record unchanged apart from the three it owns |
| `PATCH .../contact` on an unknown id | `404`, and **no item created** — `UpdateItem` upserts, so the condition expression is what prevents a half-employee |
| Second `DELETE` | `200`, keeps the first stamp |

**IAM.** A full 30-person seed issues 30 create `POST`s, 192 checklist `PATCH`es, 30
promotion/manager-link `POST`s, 20 lifecycle `DELETE`s, and three verification `GET`s through the
narrowed per-function roles with no `AccessDenied` — the thing moto cannot tell you.

One closing note for reviewers: `describe-table` now says almost nothing about this design. The
embedded `checklist` is invisible to it, where previously the shape could at least be inferred from
`SK` being a range key. This file is where the layout lives.

---

## Documents are not in this table

Uploaded documents live in S3 at `employees/<employeeId>/<slot>`, and **nothing about them is stored
here** — no filename, no size, no "has a resume" flag. `GET /employees/{id}/documents` reads S3
directly, three `HeadObject` calls, and `GET /employees/{id}` does not mention documents at all.

The reason is not storage cost, it is that **there would be no trustworthy writer for the copy.**
Only two things could maintain a `documents` map:

1. **The browser, confirming after its upload.** That endpoint would also let an employee *assert* a
   document exists that does not — and a document's presence is precisely the evidence that the
   employee supplied it. That is a security regression, not a consistency one.
2. **An S3 event notification into another Lambda.** Correct, and asynchronous: HR could still load
   the page inside the window between the object landing and the notification being handled, and see
   stale state anyway. A whole new function, role and log group to remove three `HeadObject` calls.

So S3 is the only thing that knows, and any copy here would be a cache with no correct invalidation.
Three in-region `HeadObject`s are cheaper than the strongly-consistent `GetItem` that
`PATCH /employees/{id}/contact` already pays for its response.

Two consequences worth knowing:

- **Archiving needs no document step.** `DELETE /employees/{id}` touches nothing in S3, and the
  objects survive with the record they belong to — which is the point of archiving rather than
  deleting. Nothing has to be kept in sync, so nothing can be forgotten. The flip side is that
  archived documents are never cleaned up; there is no lifecycle rule that could express "N days
  after this record was archived", and that is a stated non-goal.
- **A documents column on the employee list would force a rethink.** That would be three `HeadObject`
  calls per employee inside the `Scan` that builds `GET /employees`. If that is ever wanted, it is
  the point at which a denormalised flag on the item starts to earn its keep — and the same point at
  which the `doneCount` denormalisation in the Scan note above starts to.
