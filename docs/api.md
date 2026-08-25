# API reference — Phase 2

Currently deployed at:

```
https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev
```

Stack `onboarding-system-dev`, region `eu-north-1`. Or read it back from the stack output:

```bash
export BASE_URL=$(aws cloudformation describe-stacks \
  --stack-name onboarding-system-dev \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" \
  --output text)
```

All requests and responses are JSON. There is no authentication in Phase 2 — the API is public.
Don't put real employee data in it.

## Employee object

The wire format is deliberately identical to the model the frontend renders, which is why Phase 3
only had to change the bodies of the functions in `js/store.js`.

```json
{
  "id": "E1024",
  "firstName": "Priya",
  "lastName": "Sharma",
  "email": "priya.sharma@breville.com",
  "phone": "+61 412 883 016",
  "department": "Engineering",
  "jobTitle": "Software Engineer",
  "manager": "Santosh Kumar",
  "startDate": "2026-07-06",
  "employmentType": "Full-time",
  "checklist": [
    { "id": "offer-letter", "label": "Offer letter signed", "owner": "HR", "done": true,
      "comment": "" },
    { "id": "id-proof", "label": "ID proof submitted", "owner": "Employee", "done": false,
      "comment": "Passport, not licence." }
  ],
  "status": "In Progress",
  "progress": { "done": 1, "total": 8, "percent": 13 },
  "archived": false,
  "archivedAs": "",
  "archivedAt": ""
}
```

`status` and `progress` are **derived on every read, never stored** — they cannot drift out of
sync with the checklist. They are also the only copy: the UI renders the badge and the progress bar
from these fields rather than computing its own.

`archived` says whether the employee has been taken off the list. `archivedAs` is `"Onboarding
Cancelled"` or `"Onboarded"` on an archived record and `""` on a live one; `archivedAt` is the ISO
timestamp of the archiving. Unlike `status`, these are **stored** — they record a decision someone
made at a point in time, not a fact about the checklist as it stands. See
[`DELETE /employees/{id}`](#delete-employeesid).

`id` is the **employee number**, supplied by the caller as `employeeId` when the employee is created
and immutable afterwards. It is the DynamoDB partition key, which is what makes it unique and what
makes it unchangeable — see [database-design.md](database-design.md#the-partition-key-is-the-employee-number).

`checklist` and the three archive fields are **never settable from a request body**, and neither is
`employeeId` on anything but `POST`. Send them and they're silently dropped, matching `pickEditable`
in `js/store.js`.

### Field rules

| Field | Required | Rule |
|---|---|---|
| `employeeId` | on `POST` only | `^[A-Z0-9][A-Z0-9-]{1,19}$` after trimming and upper-casing — 2–20 characters of letters, digits and hyphens. Unique; a duplicate is a `409`. Ignored on `PUT` |
| `firstName`, `lastName`, `jobTitle` | yes | non-empty |
| `email` | yes | `^[^\s@]+@[^\s@]+\.[^\s@]+$`. **Not** unique — see below |
| `department` | yes | `Engineering` \| `HR` \| `Finance` \| `Operations` |
| `employmentType` | yes | `Full-time` \| `Contract` \| `Intern` |
| `startDate` | yes | `YYYY-MM-DD`, and a real calendar date — `2026-02-30` is a `400` |
| `phone`, `manager` | no | free text |

`comment` on a checklist item is HR's free-text note about that one step — "chased payroll twice,
still no bank details". Always a string, `""` when nobody has written anything, capped at **500
characters**. There is one note per item, not a thread: the API has no authentication, so there is
no author to attribute a thread to.

## Errors

```json
{ "error": { "code": "ValidationError",
             "message": "Employee details are not valid.",
             "fields": { "email": "Enter a valid email address." } } }
```

| Code | When |
|---|---|
| `400` `ValidationError` | malformed JSON, missing required field, bad enum or date, non-boolean `done`, non-text or over-long `comment`, or a PATCH body asking for nothing |
| `404` `NotFound` | unknown employee id or checklist item id |
| `409` `Conflict` | a `POST` under an `employeeId` that is already taken (carries `fields.employeeId`); a `PUT` or `PATCH` against an **archived** employee |
| `500` `InternalError` | anything unhandled — details are in CloudWatch, never in the response |

---

## Endpoints

### `GET /employees`

Returns every **active** employee, each with their full checklist. Archived employees are excluded —
this endpoint is what decides they are "removed", and there is no flag to include them.

```bash
curl -s "$BASE_URL/employees"
```

```json
{ "employees": [ { "id": "...", "checklist": [...] } ], "count": 6 }
```

The checklist is included on purpose: the list view filters by derived status and draws a
progress bar, both of which need it. See the module docstring in `src/handlers/list_employees.py`
for the Scan-vs-Query reasoning.

Sorted by `startDate`, then `lastName`.

### `POST /employees`

Creates the employee as a single item, with the 8 checklist entries embedded on it. Returns `201`
with the created employee and a `Location` header.

```bash
curl -s -X POST "$BASE_URL/employees" \
  -H 'Content-Type: application/json' \
  -d '{
    "employeeId": "E1024",
    "firstName": "Priya",
    "lastName": "Sharma",
    "email": "priya.sharma@breville.com",
    "phone": "+61 412 883 016",
    "department": "Engineering",
    "jobTitle": "Software Engineer",
    "manager": "Santosh Kumar",
    "startDate": "2026-07-06",
    "employmentType": "Full-time"
  }'
```

`employeeId` is **required and unique**. It is trimmed and upper-cased, then becomes the record's
`id` and its partition key, so `e1024` and `E1024` are the same employee. Posting under a number
that already exists — including one belonging to an **archived** employee, whose item is still in
the table — returns `409` with `fields.employeeId` set:

```json
{ "error": { "code": "Conflict",
             "message": "Employee ID E1024 is already taken.",
             "fields": { "employeeId": "That employee ID is already in use." } } }
```

That guarantee comes from DynamoDB itself: the write is a conditional `PutItem` on
`attribute_not_exists(employeeKey)`, so there is no read-then-write race to lose and no way for a duplicate
to slip through under load.

**Work emails, by contrast, are not unique.** Posting an address another employee already holds
succeeds. DynamoDB can only enforce uniqueness on a partition key, that key is the employee number,
and the guard item that used to carry the email constraint went when the table collapsed to one item
per employee. Callers that care have to check for themselves.

### `GET /employees/{id}`

```bash
curl -s "$BASE_URL/employees/$EMPLOYEE_ID"
```

One GetItem on the partition key returns the whole employee, checklist included — see
[database-design.md](database-design.md). `404` if the id is unknown.

The `{id}` in the path is the employee number, and it is trimmed and upper-cased before the lookup on
every route that takes one — `GET`, `PUT`, `PATCH` and `DELETE` alike — so `/employees/e1024` finds
`E1024`. People type employee numbers; they never typed UUIDs.

### `PUT /employees/{id}`

Full replace of the editable fields. **Checklist progress is preserved** — the checklist is an
attribute of the same item, and the `SET` clause is built from a whitelist that never names it.
`409` if the employee is archived.

**There is no rename.** `employeeId` is not on that whitelist, so sending one is silently dropped
rather than honoured or rejected. DynamoDB cannot move an item between partition keys: a `PUT` that
appeared to rename would have upserted a second employee and left the first one in place.

```bash
curl -s -X PUT "$BASE_URL/employees/$EMPLOYEE_ID" \
  -H 'Content-Type: application/json' \
  -d '{ "firstName": "Priya", "lastName": "Sharma",
        "email": "priya.sharma@breville.com", "phone": "+61 400 000 000",
        "department": "Engineering", "jobTitle": "Senior Software Engineer",
        "manager": "Santosh Kumar", "startDate": "2026-07-06",
        "employmentType": "Full-time" }'
```

`404` if the id is unknown — enforced by a condition expression, because `UpdateItem` would
otherwise upsert a half-employee with no checklist behind it. Changing the email is an ordinary
field edit; it is not checked against anyone else's.

The response is read back with a **consistent** read, so it always shows the values just written.

### `DELETE /employees/{id}`

**Archives. Does not delete.** Nothing is removed from the table. The profile is stamped with a
terminal state, the employee drops out of `GET /employees`, and the record stops accepting writes.

Which state depends on where the checklist had got to at that moment:

| Checklist | `archivedAs` |
|---|---|
| all 8 ticked | `Onboarded` |
| anything less | `Onboarding Cancelled` |

Returns `200` with the archived employee — not the old `204` — so the caller can report which state
it landed in without re-deriving the rule. `404` if the id is unknown.

```bash
curl -s -X DELETE "$BASE_URL/employees/$EMPLOYEE_ID"
```

```json
{ "id": "...", "status": "In Progress",
  "progress": { "done": 2, "total": 8, "percent": 25 },
  "archived": true, "archivedAs": "Onboarding Cancelled",
  "archivedAt": "2026-08-24T02:15:00Z" }
```

Note `status` still reads `In Progress`: it goes on describing the checklist, while `archivedAs`
records the decision. An archived record showing 2 of 8 is correct, not a contradiction.

Three consequences, all deliberate:

- **The work email is not reserved.** The archived record still shows it, but nothing stops a new
  hire taking the same address — re-hiring under it returns `201`, not `409`.
- **The record freezes.** `PUT` and `PATCH` both return `409` with
  `"This employee is archived. Their record is read-only."` This is enforced by a condition on the
  write itself, not just a check before it, so a cancelled onboarding cannot be ticked to 8 of 8
  afterwards and left still claiming it was cancelled.
- **Repeating it is idempotent.** A second `DELETE` returns `200` with the *first* stamp — it does
  not move `archivedAt` or recompute `archivedAs`.

Archived employees remain readable at `GET /employees/{id}` and in the DynamoDB console. There is no
un-archive endpoint; reversing one means clearing `archivedAs` on the item directly.

### `PATCH /employees/{id}/checklist/{itemId}`

Ticks or unticks one checklist item **and/or** sets its comment. Returns the full updated employee —
read consistently, so the recomputed status reflects this tick — so the caller can re-render from the
response rather than trusting its own checkbox.

Whichever keys the body carries are the ones that change. That is what makes this one endpoint
enough for both: a comment does not disturb the tick, and a tick does not disturb the comment.

```bash
# tick it
curl -s -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/offer-letter" \
  -H 'Content-Type: application/json' -d '{"done": true}'

# leave the tick alone, add a note
curl -s -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/bank-details" \
  -H 'Content-Type: application/json' -d '{"comment": "Chased payroll twice."}'

# both at once
curl -s -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/laptop" \
  -H 'Content-Type: application/json' -d '{"done": true, "comment": "Dell XPS, collected Friday."}'

# clear the note
curl -s -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/laptop" \
  -H 'Content-Type: application/json' -d '{"comment": ""}'
```

| Body | Result |
|---|---|
| `{"done": true}` | ticks it, comment untouched |
| `{"comment": "..."}` | sets the note, tick untouched |
| `{"done": …, "comment": …}` | both |
| `{"comment": ""}` or `{"comment": null}` | clears the note (the attribute is removed, not stored empty) |
| `{}` | `400` — a request that asks for nothing is a bug at the caller, not a no-op |

`comment` must be a string and is trimmed. Over 500 characters is a `400` naming the field.

Valid `itemId` values: `offer-letter`, `id-proof`, `bank-details`, `laptop`, `email-account`,
`access-card`, `induction`, `policy-ack`.

---

## Acceptance run

The sequence that proves Phase 2 is done. Every line should print the status code on the right.

```bash
# The id is ours to choose now, so there is nothing to capture from the response.
EMPLOYEE_ID=E9001
BODY='{"employeeId":"E9001","firstName":"Test","lastName":"Hire",
       "email":"test.hire@breville.com","phone":"","department":"Engineering",
       "jobTitle":"Engineer","manager":"","startDate":"2026-09-01",
       "employmentType":"Full-time"}'

curl -s -o /dev/null -w 'create                %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d "$BODY"
curl -s -o /dev/null -w 'list                  %{http_code}\n' "$BASE_URL/employees"
curl -s -o /dev/null -w 'get                   %{http_code}\n' "$BASE_URL/employees/$EMPLOYEE_ID"
curl -s -o /dev/null -w 'get, wrong case       %{http_code}\n' "$BASE_URL/employees/e9001"
curl -s -o /dev/null -w 'duplicate id          %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d "$BODY"
curl -s -o /dev/null -w 'malformed id          %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d '{"employeeId":"E 900 1","firstName":"Test","lastName":"Hire","email":"x@breville.com","department":"HR","jobTitle":"X","startDate":"2026-09-01","employmentType":"Intern"}'
curl -s -o /dev/null -w 'tick offer-letter     %{http_code}\n' -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/offer-letter" -H 'Content-Type: application/json' -d '{"done":true}'
curl -s -o /dev/null -w 'tick unknown item     %{http_code}\n' -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/not-a-thing" -H 'Content-Type: application/json' -d '{"done":true}'
curl -s -o /dev/null -w 'bad email             %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d '{"email":"nope"}'
curl -s -o /dev/null -w 'impossible date       %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d '{"employeeId":"E9002","firstName":"Test","lastName":"Hire","email":"y@breville.com","department":"HR","jobTitle":"X","startDate":"2026-02-30","employmentType":"Intern"}'
curl -s -o /dev/null -w 'archive               %{http_code}\n' -X DELETE "$BASE_URL/employees/$EMPLOYEE_ID"
curl -s -o /dev/null -w 'get after archive     %{http_code}\n' "$BASE_URL/employees/$EMPLOYEE_ID"
curl -s -o /dev/null -w 'update after archive  %{http_code}\n' -X PUT "$BASE_URL/employees/$EMPLOYEE_ID" -H 'Content-Type: application/json' -d '{"firstName":"Test","lastName":"Hire","email":"t@b.com","department":"HR","jobTitle":"X","startDate":"2026-09-01","employmentType":"Intern"}'
curl -s -o /dev/null -w 'tick after archive    %{http_code}\n' -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/id-proof" -H 'Content-Type: application/json' -d '{"done":true}'
curl -s -o /dev/null -w 'id still taken        %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d "$BODY"
curl -s -o /dev/null -w 're-hire on same email %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d '{"employeeId":"E9003","firstName":"Test","lastName":"Hire","email":"test.hire@breville.com","department":"HR","jobTitle":"X","startDate":"2026-09-01","employmentType":"Intern"}'
```

Expected:

```
create                201
list                  200
get                   200
get, wrong case       200   <- the id is folded before the lookup
duplicate id          409   <- the guarantee the key change buys
malformed id          400
tick offer-letter     200
tick unknown item     404
bad email             400
impossible date       400
archive               200
get after archive     200
update after archive  409
tick after archive    409
id still taken        409   <- archiving does not free the number
re-hire on same email 201   <- emails are still not reserved
```

Note the last two lines together. The same employee number is refused and the same work email sails
through, and that is the design rather than an inconsistency: the partition key enforces one
constraint and cannot enforce the other.

Three more checks that curl can't make for you:

1. **Persistence** — run `GET /employees` from a fresh terminal minutes later. The employee is
   still there. This is the thing Phase 1 could not do. The item's `employeeKey` reads `EMP#E9001` in the
   console, so the key names the person without a lookup.
2. **Off the list, not gone** — after the archive, confirm the employee is absent from
   `GET /employees` and still present in the DynamoDB console: one item carrying `archivedAs`, with
   its 8-entry `checklist` list intact.
3. **Comments survived** — any note written on a checklist item is still on the archived record.
   That history is the reason the item is still there.
