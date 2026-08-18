# Employee Management & Onboarding System

A lightweight internal tool for Breville HR/Admin users to manage employee records and
track onboarding progress for new hires.

**Current state: Phase 1 — UI shell with mock data. No backend, no persistence.**

Made by Abhishek.

---

## Running it

Open `index.html` in a browser. That's the whole setup — no install, no build step,
no server.

```
start index.html
```

Data lives in memory only: a refresh resets everything back to the six seeded
employees. This is deliberate for Phase 1 — see [Why no localStorage](#why-no-localstorage).

## What works

| Brief task | Status |
|---|---|
| 1. Employee list view | Done — table, live search, department + status filters |
| 2. Add Employee form | Done — validation, adds to the in-memory list |
| 3. Edit Employee view | Done — pre-filled form, plus delete |
| 4. Onboarding checklist per employee | Done — 8 items, progress bar, derived status |
| 5. Review with Santosh | Pending |

Routes are linkable and the browser back button works:

```
#/employees                      employee list
#/employees/new                  add form
#/employees/:id/edit             edit form
#/employees/:id/checklist        onboarding checklist
```

## Project structure

```
index.html          page shell + #app mount point
css/styles.css      ~60 rules, structure only
js/
  data.js           mock employees, checklist template, model helpers
  store.js          async CRUD  <-- the seam that becomes the real API
  ui.js             pure render functions (state in, HTML string out)
  app.js            hash router, event wiring, form validation
```

Scripts are classic `<script>` tags sharing a `window.App` namespace rather than ES
modules, because browsers block module imports over `file://` and the point of Phase 1
is that `index.html` opens by double-clicking it.

## Data model

```js
{
  id: "emp-001",
  firstName, lastName, email, phone,
  department,        // Engineering | HR | Finance | Operations
  jobTitle,
  manager,
  startDate,         // "2026-09-01"
  employmentType,    // Full-time | Contract | Intern
  checklist: [ { id, label, owner, done }, ... ]   // 8 items
}
```

The default checklist: offer letter signed, ID proof submitted, bank details collected,
laptop issued, email/AD account created, building access card issued, induction session
attended, policy acknowledgement signed.

## Design decisions worth knowing

### `store.js` is the API seam

Every read and write goes through `App.store`, and every function there is already
`async` with every caller already awaiting it. Today the bodies operate on an in-memory
array. In Phase 3 they become `fetch()` calls to API Gateway and **nothing outside that
one file changes.**

Callers also get deep copies, never live references — the only way to change stored data
is through a store function, which is the same contract a real HTTP API gives you. Some
ceremony now buys no surprise refactors later.

### Status is computed, not stored

There is no `status` field on an employee. It's derived from the checklist every time
it's displayed: 0 items done → Pending, all done → Onboarded, otherwise In Progress.
It cannot drift out of sync with the thing it describes.

**Open question for review:** if HR needs to set status manually (on hold, offer
withdrawn), it stops being derivable and becomes a real field in Phase 2.

### The checkbox is never the source of truth

Toggling a checklist item writes to the store, then re-renders the view from what the
store returns. The DOM is always downstream of state. This is the habit that makes the
backend swap uneventful rather than a rewrite.

### Add and Edit share one render function

`ui.formView(employee)` renders both, parameterised by an optional employee. Two
near-identical forms drift apart; one form can't.

### Why no localStorage

Persistence now would create a stale-cache problem to unwind the moment the real API
arrives. Refresh-resets-to-mock-data is the honest Phase 1 behaviour.

## Not in Phase 1

- **Document upload** (offer letter, ID proof) — a visible stub sits on the checklist
  page marking where it goes; the storage itself is S3 in a later phase.
- **Persistence of any kind** — DynamoDB later.
- **Auth** — no login, no roles. The `owner` field on checklist items is display-only.
- **Automated onboarding triggers** — notifications, IT setup request, HR alert. These
  are SNS/SQS in a later phase.

## Questions for the review with Santosh

These shape the Phase 2 data model, so they're worth settling before backend work starts:

1. Is the onboarding checklist fixed for everyone, or does it vary by department/role?
2. Who owns each checklist item — should IT-owned items even be tickable by HR?
3. Are employees ever hard-deleted, or only deactivated? (Currently: hard delete.)
4. Should the list default to showing only in-progress hires rather than everyone?
5. Does status need a manual override, or is derived-from-checklist enough?

## Status of testing

The UI has not been run in a browser yet — it was reviewed by hand, not executed. Worth
clicking through all four views before the review meeting.
