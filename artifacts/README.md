# Interactive API reference

This artifact documents all 26 API methods declared by `template.yaml` and can send requests to the deployed AWS `dev` stage.

This is a developer reference component, not part of the main employee/HR application. From the
repository root, serve only this directory on its independent port:

```powershell
py -m http.server 8001 --directory artifacts
```

Then open <http://localhost:8001/>. It can run alongside the main application because the two entry
points use different ports and different document roots. Do not open the HTML directly, use
`127.0.0.1`, or use another port; the API and S3 CORS allowlist contains
`http://localhost:8001` exactly.

All usage, maintenance, and validation instructions for this reference component belong in this
directory. Do not add its startup instructions or implementation details to the main README.

The login form is prefilled with the real demo credentials already documented in this repository. Tokens are stored in `sessionStorage` and never written into these files. Requests that mutate live AWS data require an extra confirmation in the endpoint console.

## Contract sources

- Route inventory and AWS wiring: `../template.yaml`
- Input validation and behavior: `../src/handlers/` and `../src/common/`
- Deployed base URL: `../js/config.js`, `../docs/api.md`, and the `ApiBaseUrl` stack output declared in `../template.yaml`

This reference intentionally does not use the Postman collection as its source of truth.

Its displayed examples follow the deterministic dev seed: `E1001` (Maya Chen) is a promoted
employee, `E1011` (Chloe Davis) is her promoted intern, and `E1021` (Sophie King) is still
onboarding. Create examples start at the next unused fixture number, `E1031`, so read and update
examples do not imply that a promoted employee can be edited through the onboarding-only `PUT`.

## Complete function map

The **Every function, in call order** section documents 30 application flows, including screen loads, create/edit/archive actions, checklist updates, promotion and undo sequences, manager reassignment, employee self-service, uploads, downloads, attendance, and local-only actions. Parallel and optional requests are marked explicitly, and direct S3 traffic is distinguished from API Gateway traffic.

The low-level index at the end of that section maps all 33 functions exported by `App.store` to their endpoint or composed sequence. All 26 API routes appear in at least one flow.

The API endpoint reference is the default dashboard. Use the **Functions dashboard** button in the header to show the function map on its own; switching dashboards hides the other view so the page stays focused.

## Live verification

On 4 September 2026, all 26 method/path pairs were confirmed in the deployed API Gateway stage.
The reference was then exercised from `http://localhost:8001` in a real browser session: HR login
succeeded, all 26 endpoint consoles opened, all five attendance routes rendered, the 30-flow
dashboard rendered, and no console or network errors occurred. The deployed API and document
bucket both contain `http://localhost:8001` in their explicit CORS allowlists.

## Maintenance and validation

Keep changes to this reference component inside this directory. When the API contract changes:

1. Update `api-data.js` endpoint definitions, examples, workflows, and store-function mappings.
2. Add a new category to `tagOrder` and `tagIcons` in `app.js` when needed; otherwise its endpoint
   data exists but is not rendered.
3. Update the endpoint/flow totals and dated verification text in `index.html` and this file.
4. Run the syntax checks:

   ```powershell
   node --check artifacts/app.js
   node --check artifacts/api-data.js
   ```

5. Serve this directory on port 8001, connect using each relevant demo role, open every endpoint
   console, verify mutation buttons remain disabled until confirmed, switch to the function
   dashboard, and check the browser console/network panel for errors.
