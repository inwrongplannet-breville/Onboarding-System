# Phase 3 — manual test script

There's no JS test runner in this project, so this is the repeatable version of "click through it
and check". Takes about ten minutes.

## Setup

```bash
py scripts/seed_employees.py --wipe --seed        # six employees, varied progress
py -m http.server 8000
```

Open `http://localhost:8000` with devtools on the **Network** tab, **Disable cache** and
**Preserve log** both ticked.

## Prove the mock is gone

This is the point of the phase, so check it first.

```bash
grep -rn "Priya\|SEED_\|seedEmployees\|checklistTemplate\|emp-00" js/
```

No matches. Then in the browser console: `App.seedEmployees` → `undefined`.

Now set Network to **Offline** and reload. The list must be **empty with an error banner**. If six
people appear, something is still reading from local state and the phase isn't done.

## Happy path

Each step feeds the next, so run them in order.

| # | Do this | Expect |
|---|---|---|
| 1 | Load `#/employees` | one `GET /employees` → 200, six rows. No `OPTIONS` preflight — a plain GET with no custom headers shouldn't trigger one |
| 2 | Type in search, change both filters | **zero** new requests. Filtering is client-side |
| 3 | Add Employee → submit blank | **zero** requests; client validation short-circuits |
| 4 | Fill it in properly → submit | `POST` 201 with a `Location` header, then back to the list with the new row |
| 5 | Edit that row, change the department, save | `PUT` 200. Check the request payload has exactly nine fields |
| 6 | Open its checklist, tick three boxes | three `PATCH`es, 200 each. **One request per tick, not two** — the response is reused |
| 7 | Hard-refresh (Ctrl+Shift+R) on the checklist URL | state persisted. This is the proof the writes reached DynamoDB |
| 8 | Delete it, from the row button and from the form button | `DELETE` 204, empty response body |
| 9 | Hand-type `#/employees/emp-999/edit` | "Not found" view, **not** a banner — a stale bookmark isn't an error |
| 10 | Deep-link `#/employees/<uuid>/checklist` in a fresh tab | loads directly; proves the router handles UUIDs |

## Failure drills

The part Phase 1 had no answer for. Each one is forceable in seconds.

| Drill | How | Expect |
|---|---|---|
| API unreachable | Network → **Offline**, reload the list | banner "Could not reach the server…", `#app` reads "Could not load employees." — **not** a stuck "Loading…" |
| Bad base URL | console: `App.API_BASE_URL = 'https://nope.invalid'`, then navigate | same, with `status: 0` and `error.cause` a TypeError in the console |
| Server-side 400 | console: `App.store.createEmployee({firstName:'A', email:'nope'})` | rejects with `status: 400` and a `fields` map; in the UI those messages appear under the right inputs, with **no** banner |
| Slow save | throttle to **Slow 3G**, submit the form | button reads "Saving…" and is disabled; mashing Enter fires **one** POST, not several |
| Failed save | Offline, then submit | button restored to its label and re-enabled, banner shown, **form values not lost** |
| Failed checklist tick | Offline, tick a box | the box **snaps back** unticked and re-enables; banner shown |
| Failed delete | Offline, delete a row | banner; the row is still there and the page still works |
| Stale delete | delete a row in one tab, then delete the same row in a second tab | 404 → banner |

## The one signal that matters most

Filter the console to **Errors** and run everything above, failure drills included. You should
finish with **zero `Uncaught (in promise)` warnings**.

Before Phase 3 there were six call sites that could produce them. The only console output on a
failure now should be the single deliberate `console.error` from `showError` in `app.js`, which
exists so the stack and `error.cause` stay reachable in devtools rather than being shown to an HR
user.

## Cross-check

```bash
curl https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev/employees
```

The browser and the API should agree. If they don't, the browser is caching — hard-refresh.
