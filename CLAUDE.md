# CLAUDE.md

Guidance for AI-assisted work in this repository. Keep this file concise and operational; use the
linked documents for full contracts and design history.

## Project snapshot

Breville employee management and onboarding system:

- Vanilla JavaScript frontend with no build step or npm dependency.
- Python 3.13 Lambda handlers behind Amazon API Gateway.
- AWS SAM stack `onboarding-system-dev` in `eu-north-1`.
- Two DynamoDB tables: onboarding records and completed staff records.
- S3 document storage through presigned POST uploads.
- HS256 JWT authentication with `official` and `employee` roles.
- A standalone interactive API reference in `artifacts/`.

## Sources of truth

| Source | Use it for |
|---|---|
| `template.yaml` | Deployed routes, Lambda wiring, IAM, tables, bucket, CORS, and outputs |
| `src/handlers/` and `src/common/` | Runtime behavior, validation, authorization, and response shapes |
| `docs/api.md` | Human-readable endpoint contract and examples |
| `docs/database-design.md` | Data model, invariants, access patterns, and promotion sequences |
| `docs/design.md` | Frontend architecture and decision history |
| `artifacts/README.md` | Interactive API reference usage and synchronization rules |

Do not treat the Postman collection or artifact metadata as the primary route inventory. Derive
routes from `template.yaml`, then confirm behavior in the handlers.

## Common commands

```powershell
# Main frontend. Open http://localhost:8001.
py -m http.server 8001

# Interactive API reference. Open http://localhost:8001.
# Run this instead of the main frontend server, not at the same time.
py -m http.server 8001 --directory artifacts

# Tests; moto provides in-memory AWS services.
py -m pip install -r requirements-dev.txt
py -m pytest
py -m pytest tests/test_promotion.py -q
py -m pytest tests/test_promotion.py::test_an_intern_named_as_a_reporting_manager_is_rejected

# Reset deployed dev data through the public API, not direct table writes.
py scripts/seed_employees.py --wipe --seed --yes

# Validate, build, and deploy.
sam validate --lint --region eu-north-1
sam build
sam deploy
py scripts/seed_accounts_secret.py  # first deployment only
sam sync --watch
```

`samconfig.toml` supplies the stack name, region, capabilities, and deployment bucket behavior.
Deploys confirm the change set by default. Use `--no-confirm-changeset` only for an intentional
non-interactive deployment.

The browser origin is exact: `http://localhost:8001`. Do not use `file://` or `127.0.0.1` for live
requests. Changing `AllowedOrigin` in `template.yaml` has no effect on AWS until the stack is
deployed. Before deleting the stack, empty the S3 bucket named by the `DocumentsBucketName` output.

## API and artifact

`template.yaml` currently declares 21 API method/path pairs. The artifact provides two views:

- **API dashboard:** every route, request schema, authentication requirement, and a live request
  console pointed at the deployed dev API.
- **Functions dashboard:** application workflows showing endpoint call order, including optional,
  parallel, and direct-to-S3 steps.

The artifact is static and has no AWS stack of its own. It stores tokens only in `sessionStorage`
and asks for confirmation before mutating live data. When routes change, update these together:

1. `template.yaml` and the relevant handler.
2. Tests.
3. `docs/api.md`.
4. `docs/Employee-Onboarding.postman_collection.json`.
5. `artifacts/api-data.js` and any affected workflow in `artifacts/app.js`.

Route semantics that are easy to confuse:

- `POST /employees` creates a new onboarding record. The caller supplies the immutable employee ID.
- `PUT /employees/{id}` updates an existing, active onboarding record. It never creates a record;
  unknown or promoted IDs return 404.
- `GET /employees/{id}` follows a person across onboarding and completed-staff storage.
- `DELETE /employees/{id}` archives an onboarding record in place; it does not hard-delete it.

## Data model invariants

### `OnboardingTable`

- Holds people whose onboarding is unfinished.
- Partition key: `employeeKey`, formatted as `EMP#<employeeId>` by `common/keys.py`.
- No sort key; the checklist is embedded in the employee item.
- `entityType` is always `Employee` and is required by list filtering.

### `EmployeeTable`

- Holds completed employees and interns together.
- `entityType` distinguishes `Employee` from `Intern`.
- Only interns carry `reportingManagerId`, making `ByReportingManager` a sparse GSI.

Never add a separate intern key shape or concatenate `EMP#` outside `common/keys.py`. Employee IDs
are immutable. When reading `EmployeeTable`, existence alone does not prove the record is an
employee; use `is_employee_item()` or `is_intern_item()` where the distinction matters.

`common/repository.find_record(id)` checks onboarding first and completed staff second. Use it for
ID-addressed operations that must follow a promoted record, such as profile reads, self-service
contact updates, and document access. Onboarding-only handlers deliberately use `load_employee()`.

`status` and `progress` are derived only in `common/models.py`; do not store or recompute them in
handlers or the frontend. Missing and empty optional values have the same API meaning.

## Promotion and reversal

Promotion, undo, and manager reassignment are client-driven sequences of small endpoints, not
server-side DynamoDB transactions. Preserve both guarantees:

1. Every step is idempotent.
2. The destructive step runs last.

An interruption may leave a harmless duplicate visible in two dashboards, but it must never make
the record disappear. Re-running from step one must finish safely. The destructive endpoints carry
server-side guards; do not weaken them or grant `dynamodb:DeleteItem` to additional functions
without revisiting `docs/database-design.md`.

## Request, auth, and response conventions

- Wrap every API Lambda in `@api_handler` from `common/handler.py`.
- Check authorization before parsing the body or accessing DynamoDB.
- Use `require_official`, `require_self`, or `require_role`; UI route guards are not security.
- Every route except `POST /login` uses the Lambda authorizer.
- `POST /login` has no DynamoDB permission. Employee usernames are their employee IDs.
- Build responses through `common/responses.py` for consistent envelopes and CORS headers.
- Preserve conditional writes: DynamoDB `UpdateItem` otherwise behaves as an upsert.
- Archive records with history. Hard deletes exist only in guarded promotion/reversal flows.

## Frontend boundaries

Classic script order in `index.html` is load-bearing:

`config.js -> auth.js -> store.js -> ui.js -> app.js`

- `js/store.js` owns API `fetch()` calls. Direct presigned S3 upload is the exception.
- `js/ui.js` is pure state-to-HTML rendering.
- `js/app.js` owns routing, state, DOM access, and event wiring after each full repaint.
- The frontend has no mock-data fallback; surface real request failures.

## Change checklist

Before handing off a change:

1. Run focused tests, then the full suite when behavior changed broadly.
2. Run `sam validate --lint --region eu-north-1` after template changes.
3. Keep route documentation, Postman, and artifact metadata synchronized.
4. Verify both roles and negative authorization paths for protected endpoints.
5. Confirm conditional-write, archive, and promotion invariants still hold.
6. Run JavaScript syntax checks for artifact changes:

   ```powershell
   node --check artifacts/app.js
   node --check artifacts/api-data.js
   ```

## Known deferred work

- No automatic server-side compensation for interrupted promotion sequences.
- Promotion undo is limited to seven days; full onboarding delete-and-recreate has no undo.
- Work-email uniqueness is not enforced.
- SNS/SQS onboarding notifications and IT setup requests are not implemented.
