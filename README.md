# Employee Management & Onboarding System

A lightweight internal tool for Breville HR/Admin users to manage employee records and track
onboarding progress for new hires.

Vanilla JS frontend, Python Lambdas behind API Gateway, DynamoDB. No build step, no npm.

Made by Abhishek.

---

## Run the UI

```bash
py -m http.server 8000
```

Then open **http://localhost:8000**.

No install and no build step — but **don't double-click `index.html`**. A `file://` page has an
opaque origin and sends `Origin: null` on every request, which browsers treat inconsistently; a
CORS rejection there looks exactly like a broken API.

Everything you see comes from DynamoDB. Add someone, refresh, and they're still there.

## Reset the data

Wipes the table and repopulates it with six employees at varied onboarding stages:

```bash
py scripts/seed_employees.py --wipe --seed
```

Add `--yes` to skip the confirmation prompt. It drives the public API, not the table directly.

The six fixtures take employee numbers `E1001`–`E1006`. Those numbers are the DynamoDB partition
key, so `--seed` without `--wipe` now fails loudly with a `409` instead of quietly creating a second
copy of everyone.

## Run the backend tests

```bash
py -m pip install -r requirements-dev.txt
py -m pytest                    # 194 tests, ~30 seconds
```

No AWS account or credentials needed — the handler tests run against an in-memory DynamoDB.

## Deploy the backend

Needs the [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html),
the AWS CLI, and Python 3.13. Docker is *not* required.

```bash
sam validate --lint
sam build
sam deploy                      # samconfig.toml is committed, so no arguments needed
sam sync --watch                # ~5s code pushes while iterating
sam delete                      # tear the whole stack down
```

Already deployed to `onboarding-system-dev` in `eu-north-1`. The stack's `ApiBaseUrl` output is the
value in `js/config.js` — update that one line if you deploy your own.

## Current deployment

| | |
|---|---|
| API | `https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev` |
| Stack | `onboarding-system-dev`, `eu-north-1` |
| Table | `onboarding-dev` |

> The API is **public and unauthenticated**. It's a learning stack — don't put real employee data
> in it, and `sam delete` when you're done.

---

## Docs

| | |
|---|---|
| [docs/design.md](docs/design.md) | Architecture, data model, and why things are built the way they are |
| [docs/database-design.md](docs/database-design.md) | The DynamoDB layout in full — item shape, access patterns, invariants, cost |
| [docs/api.md](docs/api.md) | Endpoint reference with curl examples |
| [docs/phase3-testing.md](docs/phase3-testing.md) | Manual browser click-through |
| [Postman collection](docs/Employee-Onboarding.postman_collection.json) | Import and run top to bottom |

## Project status

Phases 1–4 of the brief are complete, including the review with Santosh: UI shell, CRUD backend on
AWS, the two connected, and the onboarding checklist tracked per employee end to end.

`DELETE /employees/{id}` archives rather than erases — the employee leaves the list, the record and
its checklist stay in DynamoDB stamped `Onboarded` or `Onboarding Cancelled`, and stop accepting
writes. That reverses decision 3 from the review; the reasoning is in
[design.md](docs/design.md#number-3-reversed-archiving-instead-of-deleting).

The partition key is the **employee number** HR types on the form (`EMP#E1024`), not a generated
UUID. That is what makes the employee number unique — DynamoDB can enforce uniqueness on a partition
key and on nothing else — and it is also why the number can never be edited afterwards. See
[database-design.md](docs/database-design.md#the-partition-key-is-the-employee-number).

Phase 5 (S3 document upload, SNS/SQS onboarding triggers) is not started.

The five data-model decisions that came out of the review are recorded in
[design.md](docs/design.md#data-model-decisions-from-the-review).
