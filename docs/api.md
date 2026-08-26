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

All requests and responses are JSON.

## Authentication

Every route except `POST /login` sits behind a Lambda authorizer and needs a bearer token:

```bash
export TOKEN=$(curl -s -X POST $BASE_URL/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"hr.admin","password":"onboard-2026"}' | py -c "import json,sys; print(json.load(sys.stdin)['token'])")

curl -s $BASE_URL/employees -H "Authorization: Bearer $TOKEN"
```

`POST /login` takes `{username, password}` and returns:

```json
{ "token": "eyJhbGci...", "role": "official", "displayName": "HR Admin", "expiresIn": 28800 }
```

Wrong credentials are `401` with one generic message, whether the username exists or not.

**An employee's username is their employee number.** There is no `employee` account; every number
verifies against one shared password, and the number becomes the token's `sub`:

```bash
curl -s -X POST $BASE_URL/login -H 'Content-Type: application/json' \
  -d '{"username":"E1001","password":"welcome-2026"}'
```

```json
{ "token": "eyJhbGci...", "role": "employee", "displayName": "E1001", "expiresIn": 28800 }
```

`e1001` works too — the number is trimmed and upper-cased into the token so it matches the partition
key. `displayName` is the number and not a name, because this route **cannot read the employee
table**: `LoginFunction` has no DynamoDB permission at all. The same constraint means a number with
no record behind it signs in successfully; `E9999` gets a token and a `404` on its first read.

### Roles

Two, carried as a claim inside the signed token — see `src/common/accounts.py`.

| | `official` | `employee` |
|---|---|---|
| `GET /employees` | the whole list | `403 Forbidden` |
| `GET /employees/{id}` | any employee, in full | **their own record only**, in full; `403` for anyone else's |
| `POST`, `PUT`, `DELETE`, `PATCH …/checklist/…` | allowed | `403 Forbidden` |
| `PATCH /employees/{id}/contact` | `403 Forbidden` | **their own record only** |
| `GET /employees/{id}/documents` | any employee's | **their own record only** |
| `POST /employees/{id}/documents/{slot}` | `403 Forbidden` | **their own record only** |
| Archived records | readable by id | their own, readable and frozen; `403` for anyone else's |

An employee is scoped to one record, not to a subset of fields. `GET /employees/{id}` on their own
number returns the whole profile — work email, manager, employment type, the checklist, the archive
flags — with exactly one thing withheld: the **`comment` on each checklist item**. Ticks are facts
about the hire; a comment is an HR working note written by one official for another.

That view is built by naming what goes in (`own_profile_view` in `src/common/models.py`), so a field
added to the model later is invisible to the employee until somebody deliberately exposes it. The
write path returns the same shape, which is what stops a successful `PATCH` handing back the comments
its matching `GET` withheld.

Asking about somebody else is a `403`, and it is **the same `403` whether or not that record
exists** — the check runs before the read, so walking the id space tells an employee nothing about
who is in the table.

The scoping is a boundary on the *product*, not on confidentiality: the employee password is shared
and numbers are sequential, so one leaked password reads any record one number at a time. See
[design.md](design.md).

### Status codes

| | |
|---|---|
| `401 Unauthorized` | No token, a malformed one, a bad signature, an expired one, or one issued for another deployment. The session is over — sign in again. Emitted by API Gateway, not by a handler. |
| `403 Forbidden` | A valid token whose role does not permit this. The session is fine; the action is not theirs. Also returned if a request somehow arrives with no authorizer context at all, which is a misconfiguration rather than a caller problem. |

Tokens carry `iss` (`onboarding-system`) and `aud` (the stage name) and both are checked, so a token
minted against `dev` is refused by `prod` even if the two stacks were deployed with the same key.

`POST /login` is throttled at the gateway — 30 requests/second sustained, burst 10. That is a
stage-wide ceiling rather than per-IP: it caps the bill and the guessing rate, it does not identify
a caller.

Only the origin in the stack's `AllowedOrigin` parameter may call this API from a browser
(`http://localhost:8000` by default).

The distinction is load-bearing for the frontend: `js/store.js` signs the user out on a 401 and
passes a 403 through to the caller. Don't collapse them.

Don't put real employee data in this stack — the credentials above are in a public repo.

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
  "personalEmail": "priya@example.com",
  "address": "12 Smith Street, Sydney NSW 2000",
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

`personalEmail` and `address` are the employee's own. They are **absent from the officials
whitelist**, so `PUT` cannot set them and the HR form has no control for them — the only route that
writes them is [`PATCH /employees/{id}/contact`](#patch-employeesidcontact). Both are `""` until
somebody fills them in.

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
| `phone`, `manager` | no | free text. `phone` is capped at 40 characters and has two writers — HR's `PUT` and the employee's own `PATCH` |
| `personalEmail` | no | same email pattern as `email`. Settable **only** by the employee, on `PATCH …/contact` |
| `address` | no | free text, capped at **300 characters**. Settable **only** by the employee, on `PATCH …/contact` |

`comment` on a checklist item is HR's free-text note about that one step — "chased payroll twice,
still no bank details". Always a string, `""` when nobody has written anything, capped at **500
characters**. There is one note per item, not a thread — see
[design.md](design.md#comments-on-checklist-items) for why. Employees never see comments at all,
including on their own record — the field is absent from the response, not blanked.

## Errors

```json
{ "error": { "code": "ValidationError",
             "message": "Employee details are not valid.",
             "fields": { "email": "Enter a valid email address." } } }
```

| Code | When |
|---|---|
| `400` `ValidationError` | malformed JSON, missing required field, bad enum or date, non-boolean `done`, non-text or over-long `comment` or `address`, a non-text contact field, or a PATCH body asking for nothing |
| `403` `Forbidden` | the caller's role does not permit this route, or an employee asking about a record that is not theirs |
| `404` `NotFound` | unknown employee id or checklist item id |
| `409` `Conflict` | a `POST` under an `employeeId` that is already taken (carries `fields.employeeId`); a `PUT`, `PATCH` or document upload against an **archived** employee |
| `500` `InternalError` | anything unhandled — details are in CloudWatch, never in the response |

---

## Endpoints

### `GET /employees`

**Officials only** — `403` for an employee token. An employee has exactly one record they may read
and they reach it by id, so there is no trimmed version of this list to serve them.

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

An **employee** token may call this for their own number only, and gets the whole record minus the
checklist comments. Any other id is a `403`, identical whether or not the record exists.

```bash
# as E1001
curl -s "$BASE_URL/employees/E1001" -H "Authorization: Bearer $TOKEN"   # 200, whole record
curl -s "$BASE_URL/employees/E1002" -H "Authorization: Bearer $TOKEN"   # 403
curl -s "$BASE_URL/employees/E9999" -H "Authorization: Bearer $TOKEN"   # 403, same body
```

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

### `PATCH /employees/{id}/contact`

The one write an employee may make: **their own** phone, personal email and home address. `403` for
an official (they have `PUT`) and `403` for an employee naming somebody else's id.

Its own sub-resource, so the path says what may change — `/employees/E1024/contact` cannot be
mistaken for a way to edit the employment. And a `PATCH` rather than a `PUT`, because all three
fields are optional: a full replace would let a curl naming one field silently clear the other two
with no required-field check to catch it.

Present-key semantics, matching the checklist `PATCH`:

| Body | Effect |
|---|---|
| `{"phone": "+61 400 111 222"}` | sets `phone`, leaves the other two alone |
| `{"address": ""}` or `{"address": null}` | clears `address` |
| `{}` or `{"department": "HR"}` | `400` — nothing it may write was named |
| `{"phone": 5}` | `400` under `fields.phone`. A non-string is refused, **not** coerced to `""` — coercing would turn a client bug into a deletion |
| `{"firstName": "…", "checklist": []}` alongside a real field | the extras are silently dropped, exactly as `PUT` drops `checklist` |

```bash
curl -s -X PATCH "$BASE_URL/employees/E1001/contact" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{ "phone": "+61 400 111 222",
        "personalEmail": "priya@example.com",
        "address": "12 Smith Street, Sydney NSW 2000" }'
```

`404` if the id has no record — enforced by the same condition expression `PUT` uses, because
`UpdateItem` would otherwise upsert a half-employee with no checklist behind it. This is where a
mistyped employee number from `POST /login` finally surfaces. `409` if the record is archived.

Returns the employee's own view of the whole record, read **consistently** — the same shape
`GET /employees/{id}` gives that caller, comments withheld, so the UI can repaint straight from it.
The checklist, its comments and every HR-owned field are untouched: the `SET` clause is built from a
three-field whitelist and never names them.

### `GET /employees/{id}/documents`

The three document slots for one employee. An official reads anybody's; an employee reads their own
and gets `403` for anyone else's — **the same `403` whether or not that record exists**, because the
check runs before anything is read.

```bash
curl -s "$BASE_URL/employees/E1001/documents" -H "Authorization: Bearer $TOKEN"
```

```json
{ "documents": [
    { "slot": "resume", "label": "Resume", "uploaded": true,
      "filename": "Priya Sharma CV.pdf", "contentType": "application/pdf",
      "size": 245760, "uploadedAt": "2026-09-12T04:11:07Z",
      "downloadUrl": "https://…s3…?X-Amz-Signature=…" },
    { "slot": "id-document", "label": "ID document", "uploaded": false },
    { "slot": "signed-offer-letter", "label": "Signed offer letter", "uploaded": false }
  ] }
```

**Always three slots, in that order, uploaded or not.** An empty slot is not omitted and not a 404 —
it carries `uploaded: false` and **no `downloadUrl` key at all**, so there is nothing a client could
link to by mistake. That is what lets the UI draw an empty drop zone: absence is data.

`downloadUrl` is signed and expires in **five minutes**. It forces a download (rather than a preview)
under the original filename, both pinned into the signature so a client cannot change either. Don't
cache these; ask again.

There is no DynamoDB read here at all, so an employee number with no record answers three empty
slots rather than a 404. S3 is the only source of truth for what has been uploaded — see
[design.md](design.md).

### `POST /employees/{id}/documents/{slot}`

Asks for permission to upload, and returns a ticket. **The file is not sent to this route.** It is
sent by the browser straight to S3, using the presigned form below, so a document never passes
through API Gateway (10 MB request cap) or Lambda (6 MB payload cap).

`{slot}` is one of `resume`, `id-document`, `signed-offer-letter`. Anything else is a `400` that does
not echo what you sent.

**Employee only, own record only.** An official gets `403` — they have no upload route, which is what
makes a document's presence evidence that the *employee* supplied it. `404` if the employee number
has no record; `409` if the record is archived.

```bash
curl -s -X POST "$BASE_URL/employees/E1001/documents/resume" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{ "filename": "Priya Sharma CV.pdf", "contentType": "application/pdf" }'
```

```json
{ "slot": "resume",
  "filename": "Priya Sharma CV.pdf",
  "url": "https://<bucket>.s3.eu-north-1.amazonaws.com/",
  "fields": { "key": "employees/E1001/resume", "Content-Type": "application/pdf",
              "x-amz-meta-filename": "Priya Sharma CV.pdf", "policy": "…",
              "x-amz-algorithm": "…", "x-amz-credential": "…",
              "x-amz-date": "…", "x-amz-signature": "…" } }
```

`filename` comes back **sanitised** — that is the name that will be stored, not necessarily the one
you sent. `contentType` must be one of `application/pdf`, `image/jpeg`, `image/png` or the Word
`.docx` type; it is client-asserted and unverified, but it is pinned into the policy, so the stored
object cannot disagree with what was declared.

Then post the file to `url`:

```bash
curl -s -X POST "<url>" \
  -F key=employees/E1001/resume \
  -F Content-Type=application/pdf \
  -F x-amz-meta-filename="Priya Sharma CV.pdf" \
  -F policy=… -F x-amz-algorithm=… -F x-amz-credential=… \
  -F x-amz-date=… -F x-amz-signature=… \
  -F file=@"Priya Sharma CV.pdf"
```

Three rules about that form, each a `403` from S3 if broken:

- **Send every field the ticket gave you, unmodified.** They are signed.
- **Send `file` last.** S3 ignores every field after the file part, so a file-first body looks like it
  has no policy at all.
- **Send nothing else.** An extra field is `Invalid according to Policy: Extra input fields`.

Success is **`204` with an empty body**. The policy caps the upload at **10 MB** and refuses an empty
file — enforced by S3, not by the browser, which is the reason this is a presigned POST rather than a
presigned `PUT` (a `PUT` URL cannot express a maximum). The ticket expires in **five minutes**.

Uploading again to the same slot **overwrites** it. There is one object per slot and no version
history.

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
- **The record freezes.** `PUT` and both `PATCH` routes return `409` with
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

### The employee side

Officials-only above. This is the other role, end to end — a fresh token, because these are the
checks that prove the API and not the browser is doing the scoping.

```bash
E_TOKEN=$(curl -s -X POST $BASE_URL/login -H 'Content-Type: application/json' \
  -d '{"username":"E1001","password":"welcome-2026"}' \
  | py -c "import json,sys; print(json.load(sys.stdin)['token'])")
AUTH="Authorization: Bearer $E_TOKEN"

curl -s -o /dev/null -w 'own record            %{http_code}\n' "$BASE_URL/employees/E1001" -H "$AUTH"
curl -s -o /dev/null -w 'own record, lowercase %{http_code}\n' "$BASE_URL/employees/e1001" -H "$AUTH"
curl -s -o /dev/null -w 'a colleague           %{http_code}\n' "$BASE_URL/employees/E1002" -H "$AUTH"
curl -s -o /dev/null -w 'a record that is not  %{http_code}\n' "$BASE_URL/employees/E9999" -H "$AUTH"
curl -s -o /dev/null -w 'the directory         %{http_code}\n' "$BASE_URL/employees" -H "$AUTH"
curl -s -o /dev/null -w 'own contact patch     %{http_code}\n' -X PATCH "$BASE_URL/employees/E1001/contact" -H "$AUTH" -H 'Content-Type: application/json' -d '{"phone":"+61 400 111 222","personalEmail":"priya@example.com"}'
curl -s -o /dev/null -w 'another record patch  %{http_code}\n' -X PATCH "$BASE_URL/employees/E1002/contact" -H "$AUTH" -H 'Content-Type: application/json' -d '{"phone":"+61 400 000 000"}'
curl -s -o /dev/null -w 'an empty patch        %{http_code}\n' -X PATCH "$BASE_URL/employees/E1001/contact" -H "$AUTH" -H 'Content-Type: application/json' -d '{"department":"HR"}'
curl -s -o /dev/null -w 'an officials write    %{http_code}\n' -X PUT "$BASE_URL/employees/E1001" -H "$AUTH" -H 'Content-Type: application/json' -d '{"firstName":"Priya","lastName":"Sharma","email":"p@b.com","department":"HR","jobTitle":"X","startDate":"2026-09-01","employmentType":"Intern"}'
```

Expected:

```
own record            200
own record, lowercase 200   <- the id is folded on both sides
a colleague           403
a record that is not  403   <- identical body to the line above; the check precedes the read
the directory         403
own contact patch     200
another record patch  403
an empty patch        400   <- `department` is not theirs to send, so nothing was named
an officials write    403
```

Then the check no status code can make for you: read the body of `own record` and confirm that every
checklist item has `done`, `label` and `owner` and **no `comment` key at all** — and that the body of
`own contact patch` says the same. The write path is the one that would leak, because a re-read
returns everything and it is the trim that stops it.

Three more checks that curl can't make for you:

1. **Persistence** — run `GET /employees` from a fresh terminal minutes later. The employee is
   still there. This is the thing Phase 1 could not do. The item's `employeeKey` reads `EMP#E9001` in the
   console, so the key names the person without a lookup.
2. **Off the list, not gone** — after the archive, confirm the employee is absent from
   `GET /employees` and still present in the DynamoDB console: one item carrying `archivedAs`, with
   its 8-entry `checklist` list intact.
3. **Comments survived** — any note written on a checklist item is still on the archived record.
   That history is the reason the item is still there.
