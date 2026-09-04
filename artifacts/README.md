# Interactive API reference

This artifact documents all 26 API methods declared by `template.yaml` and can send requests to the deployed AWS `dev` stage.

From the repository root, serve it on the origin allowed by the stack:

```powershell
py -m http.server 8001 --directory artifacts
```

Then open <http://localhost:8001>. Do not open `index.html` directly and do not use `127.0.0.1`; the API and S3 CORS policies must allow `http://localhost:8001` exactly.

The login form is prefilled with the real demo credentials already documented in this repository. Tokens are stored in `sessionStorage` and never written into these files. Requests that mutate live AWS data require an extra confirmation in the endpoint console.

## Contract sources

- Route inventory and AWS wiring: `../template.yaml`
- Input validation and behavior: `../src/handlers/` and `../src/common/`
- Deployed base URL: `../js/config.js`, `../docs/api.md`, and the `ApiBaseUrl` stack output declared in `../template.yaml`

The artifact intentionally does not use the Postman collection as its source of truth.

Its displayed examples follow the deterministic dev seed: `E1001` (Maya Chen) is a promoted
employee, `E1011` (Chloe Davis) is her promoted intern, and `E1021` (Sophie King) is still
onboarding. Create examples start at the next unused fixture number, `E1031`, so read and update
examples do not imply that a promoted employee can be edited through the onboarding-only `PUT`.

## Complete function map

The **Every function, in call order** section documents 27 application flows, including screen loads, create/edit/archive actions, checklist updates, promotion and undo sequences, manager reassignment, employee self-service, uploads, downloads, and local-only actions. Parallel and optional requests are marked explicitly, and direct S3 traffic is distinguished from API Gateway traffic.

The low-level index at the end of that section maps all 33 functions exported by `App.store` to their endpoint or composed sequence. All 26 API routes appear in at least one flow.

The API endpoint reference is the default dashboard. Use the **Functions dashboard** button in the header to show the function map on its own; switching dashboards hides the other view so the page stays focused.

## Live verification

On 4 September 2026, the pre-attendance set of 21 method/path pairs was probed at the base URL without credentials. `POST /login` reached its Lambda and returned its expected validation response; all 20 protected methods reached the Lambda authorizer and returned `401`. This is a dated deployment record; the five attendance routes require a fresh probe after deployment.
