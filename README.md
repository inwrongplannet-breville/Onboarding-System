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

## Run the backend tests

```bash
py -m pip install -r requirements-dev.txt
py -m pytest                    # 78 tests, ~15 seconds
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
| [docs/api.md](docs/api.md) | Endpoint reference with curl examples |
| [docs/phase3-testing.md](docs/phase3-testing.md) | Manual browser click-through |
| [Postman collection](docs/Employee-Onboarding.postman_collection.json) | Import and run top to bottom |

## Project status

Phases 1–3 of the brief are complete, including the review with Santosh: UI shell, CRUD backend on
AWS, and the two connected. Phase 4 (S3 document upload, SNS/SQS onboarding triggers) is not
started.

The five data-model decisions that came out of the review are recorded in
[design.md](docs/design.md#data-model-decisions-from-the-review).
