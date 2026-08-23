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

The wire format is deliberately identical to the frontend model in `js/data.js`, so Phase 3 only
has to change the bodies of the functions in `js/store.js`.

```json
{
  "id": "7f3c1a2e-9b41-4d0e-8a55-c2e11d6b7a90",
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
    { "id": "offer-letter", "label": "Offer letter signed", "owner": "HR", "done": true },
    { "id": "id-proof", "label": "ID proof submitted", "owner": "Employee", "done": false }
  ],
  "status": "In Progress",
  "progress": { "done": 1, "total": 8, "percent": 13 }
}
```

`status` and `progress` are **derived on every read, never stored** — they cannot drift out of
sync with the checklist. They're additive conveniences; the Phase 1 UI computes its own and
ignores them.

`id` and `checklist` are **never settable from a request body**. Send them and they're silently
dropped, matching `pickEditable` in `js/store.js`.

### Field rules

| Field | Required | Rule |
|---|---|---|
| `firstName`, `lastName`, `jobTitle` | yes | non-empty |
| `email` | yes | `^[^\s@]+@[^\s@]+\.[^\s@]+$` |
| `department` | yes | `Engineering` \| `HR` \| `Finance` \| `Operations` |
| `employmentType` | yes | `Full-time` \| `Contract` \| `Intern` |
| `startDate` | yes | `YYYY-MM-DD` |
| `phone`, `manager` | no | free text |

## Errors

```json
{ "error": { "code": "ValidationError",
             "message": "Employee details are not valid.",
             "fields": { "email": "Enter a valid email address." } } }
```

| Code | When |
|---|---|
| `400` `ValidationError` | malformed JSON, missing required field, bad enum or date, non-boolean `done` |
| `404` `NotFound` | unknown employee id or checklist item id |
| `409` `Conflict` | id collision on create |
| `500` `InternalError` | anything unhandled — details are in CloudWatch, never in the response |

---

## Endpoints

### `GET /employees`

Returns every employee, each with their full checklist.

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

Creates the employee **and** their 8 checklist rows in one atomic transaction. Returns `201` with
the created employee and a `Location` header.

```bash
curl -s -X POST "$BASE_URL/employees" \
  -H 'Content-Type: application/json' \
  -d '{
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

### `GET /employees/{id}`

```bash
curl -s "$BASE_URL/employees/$EMPLOYEE_ID"
```

One Query on the partition key returns the profile and all 8 checklist rows together. `404` if
the id is unknown.

### `PUT /employees/{id}`

Full replace of the editable fields. **Checklist progress is preserved** — the checklist lives in
separate items that this endpoint never touches.

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
otherwise upsert a profile with no checklist behind it.

### `DELETE /employees/{id}`

Deletes the whole partition — profile and all 8 checklist rows — in one transaction. `204` with
no body, `404` if unknown.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE "$BASE_URL/employees/$EMPLOYEE_ID"
```

### `PATCH /employees/{id}/checklist/{itemId}`

Ticks or unticks one checklist item. Returns the full updated employee so the caller can re-render
from the response rather than trusting its own checkbox.

```bash
curl -s -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/offer-letter" \
  -H 'Content-Type: application/json' \
  -d '{"done": true}'
```

Valid `itemId` values: `offer-letter`, `id-proof`, `bank-details`, `laptop`, `email-account`,
`access-card`, `induction`, `policy-ack`.

---

## Acceptance run

The sequence that proves Phase 2 is done. Every line should print the status code on the right.

```bash
# 201 - capture the id
EMPLOYEE_ID=$(curl -s -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' \
  -d '{"firstName":"Test","lastName":"Hire","email":"test.hire@breville.com","phone":"",
       "department":"Engineering","jobTitle":"Engineer","manager":"",
       "startDate":"2026-09-01","employmentType":"Full-time"}' | py -c 'import json,sys;print(json.load(sys.stdin)["id"])')

curl -s -o /dev/null -w 'list                  %{http_code}\n' "$BASE_URL/employees"
curl -s -o /dev/null -w 'get                   %{http_code}\n' "$BASE_URL/employees/$EMPLOYEE_ID"
curl -s -o /dev/null -w 'tick offer-letter     %{http_code}\n' -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/offer-letter" -H 'Content-Type: application/json' -d '{"done":true}'
curl -s -o /dev/null -w 'tick unknown item     %{http_code}\n' -X PATCH "$BASE_URL/employees/$EMPLOYEE_ID/checklist/not-a-thing" -H 'Content-Type: application/json' -d '{"done":true}'
curl -s -o /dev/null -w 'bad email             %{http_code}\n' -X POST "$BASE_URL/employees" -H 'Content-Type: application/json' -d '{"email":"nope"}'
curl -s -o /dev/null -w 'delete                %{http_code}\n' -X DELETE "$BASE_URL/employees/$EMPLOYEE_ID"
curl -s -o /dev/null -w 'get after delete      %{http_code}\n' "$BASE_URL/employees/$EMPLOYEE_ID"
curl -s -o /dev/null -w 'update after delete   %{http_code}\n' -X PUT "$BASE_URL/employees/$EMPLOYEE_ID" -H 'Content-Type: application/json' -d '{"firstName":"Test","lastName":"Hire","email":"t@b.com","department":"HR","jobTitle":"X","startDate":"2026-09-01","employmentType":"Intern"}'
```

Expected:

```
list                  200
get                   200
tick offer-letter     200
tick unknown item     404
bad email             400
delete                204
get after delete      404
update after delete   404
```

Two more checks that curl can't make for you:

1. **Persistence** — run `GET /employees` from a fresh terminal minutes later. The employee is
   still there. This is the thing Phase 1 could not do.
2. **No orphans** — after the DELETE, scan the table in the DynamoDB console and confirm zero
   remaining `CHK#` items under that `PK`.
