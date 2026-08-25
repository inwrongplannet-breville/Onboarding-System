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

You land on the login page. Sign in as `hr.admin` / `onboard-2026` for everything from
"Happy path" onwards — those sections are the officials console, unchanged. The role
section below covers the other account.

## Prove the mock is gone

This is the point of the phase, so check it first.

```bash
grep -rn "Priya\|SEED_\|seedEmployees\|checklistTemplate\|emp-00" js/
grep -rn "Engineering\|Full-time\|In Progress" js/
```

No matches for either. The second is the newer rule: no employee *schema* in `js/` and not just no
employee *records*. The enums, the status names and the progress arithmetic all live in
`common/models.py` now. In the browser console: `App.seedEmployees` and `App.DEPARTMENTS` → both
`undefined`.

Now set Network to **Offline** and reload. The list must be **empty with an error banner**. If six
people appear, something is still reading from local state and the phase isn't done.

## Roles and the guard

The split is enforced by the API, so the checks worth doing are the ones that prove the
browser is not the thing enforcing it.

| # | Do this | Expect |
|---|---|---|
| 1 | Load `/` signed out | The login card. Not a flash of the employee list on the way past |
| 2 | Type `#/employees`, `#/employees/new`, `#/employees/E1001/edit`, `#/employees/E1001/checklist` into the address bar, signed out | Every one lands on `#/login` |
| 3 | Sign in with a wrong password | Inline message above the fields, password cleared, focus in the password box, and the message is announced |
| 4 | Sign in as `employee` / `welcome-2026` | The directory: six columns, no Add button, no Edit/Checklist/Delete, no actions column at all |
| 5 | As the employee, expand a row in the Network response for `GET /employees` | **No `email`, `phone`, `manager`, `employmentType` or `checklist` keys.** Absent, not empty — this is the check that matters, because the UI not drawing a field proves nothing |
| 6 | As the employee, type the four officials hashes from step 2 | Every one lands on `#/directory` |
| 7 | As the employee, search for `und` | No matches. (`applyFilters` builds its haystack from a field the employee response omits; without the coercion in `app.js` this matches every row) |
| 8 | Sign in as `hr.admin` and check any request header | `Authorization: Bearer …`, and the preflight `OPTIONS` returns 200. A failed preflight shows up here as a CORS error rather than as a 401 |
| 9 | Reload mid-session | Still signed in |
| 10 | Sign out, then press Back | The login page, not the app |
| 11 | Open a second tab | Signed out. `sessionStorage` is per tab, deliberately |

### Prove the guard is not the control

Signed in as `employee`, in the console:

```js
var s = JSON.parse(sessionStorage['onboarding.session']);
s.role = 'official';                       // there is no such field any more
sessionStorage['onboarding.session'] = JSON.stringify(s);
location.hash = '#/employees';
```

**Nothing happens** — you stay in the directory. The role is read from the token's own payload,
so there is no stored copy to edit. Adding one changes nothing.

Now tamper with the token itself:

```js
var s = JSON.parse(sessionStorage['onboarding.session']);
var p = s.token.split('.');
p[1] = btoa(JSON.stringify(Object.assign(
  JSON.parse(atob(p[1].replace(/-/g,'+').replace(/_/g,'/'))), { role: 'official' })))
  .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
s.token = p.join('.');
sessionStorage['onboarding.session'] = JSON.stringify(s);
location.hash = '#/employees';
```

The officials shell renders for an instant, the first request comes back **401**, and you land
back on the login page with **"Your session expired. Sign in again." shown on the form**. That
message being visible is the point — it used to exist only in the screen-reader live region.

That is the design: the browser picks the screen, the signature decides what the API does.

## Happy path

Each step feeds the next, so run them in order.

| # | Do this | Expect |
|---|---|---|
| 1 | Load `#/employees` | one `GET /employees` → 200, six rows. No `OPTIONS` preflight — a plain GET with no custom headers shouldn't trigger one |
| 2 | Type in search, change both filters | **zero** new requests. Filtering is client-side |
| 3 | Add Employee → submit blank | one `GET /employees` on open (the dropdowns are built from it), then **zero** requests on submit; client validation short-circuits |
| 3b | Check both dropdowns on that form | Departments and employment types are the ones the six seeded employees carry, alphabetical. Nothing hardcoded produced them |
| 4 | Fill it in properly → submit | `POST` 201 with a `Location` header, then back to the list with the new row |
| 5 | Edit that row, change the department, save | `PUT` 200. Check the request payload has exactly nine fields |
| 5b | Edit it again, set the email to another employee's, save | `200`. Work emails are no longer unique, so this is accepted — two records now share the address |
| 6 | Open its checklist, tick three boxes | three `PATCH`es, 200 each. **One request per tick, not two** — the response is reused |
| 6b | Click the comment icon on an item, type a note, Save | one `PATCH` with a body of `{"comment": …}` and no `done` key. The note appears under the item and the icon fills in |
| 6c | Tick that same item | one `PATCH` with `{"done": true}`. **The note is still there** — the two never overwrite each other, even though both now write into one item's `checklist` list |
| 6d | Open a comment box, type, then tick a *different* item without saving | the view repaints and **your half-typed text is still in the box** |
| 6e | Reopen the note and press Remove | the note and the icon fill both go |
| 6f | Click the icon, then click the same icon again | the box closes. The tooltip reads "Close the comment box on …" while it is open, and focus lands back on the icon |
| 6g | Open a box, type something, then click that icon again | it asks before discarding. Cancel and Esc don't ask — they say "discard" in as many words; the icon doesn't |
| 7 | Hard-refresh (Ctrl+Shift+R) on the checklist URL | state persisted, comments included. This is the proof the writes reached DynamoDB |
| 8 | Delete it, from the row button and from the form button | `DELETE` 200; the row leaves the list, and the item is still in the table stamped `archivedAs` |
| 8a | Open the archived employee's URL directly (`#/employees/<id>/checklist`) | banner reads "Archived … Onboarding Cancelled"; every checkbox and comment button is disabled |
| 8b | Open the archived employee's edit URL (`#/employees/<id>/edit`) | the read-only archived page, not the form |
| 8c | Re-add someone on the archived employee's work email | `201` — nothing reserves the address any more. You now have two records on one mailbox, which is the accepted cost of dropping the guard item |
| 8d | Re-add someone on the archived employee's **employee number** | `409`. The item is still in the table, so the number is still taken — the opposite of 8c, and the difference is exactly what the partition key can and cannot enforce |
| 9 | Hand-type `#/employees/emp-999/edit` | "Not found" view, **not** a banner — a stale bookmark isn't an error |
| 10 | Deep-link `#/employees/E1003/checklist` in a fresh tab | loads directly. Employee numbers are typeable, so this is now a link someone can write by hand |
| 11 | Add someone using an employee number that already exists | `409`, and the message lands **under the Employee ID input**, not in the page banner. Nothing is created, and the existing record keeps its checklist |
| 11a | Add someone with `e1024` while `E1024` exists | also `409` — ids are upper-cased before the write, so case cannot smuggle in a second record for one person |
| 11b | Add someone with `E 1024` or a 30-character id | `400` under the same input, before anything is written |
| 11c | Open an existing employee's edit form | the Employee ID box is filled, greyed and read-only, with "An employee ID cannot be changed once the record exists." under it. Saving leaves the id alone |
| 11d | Hand-type `#/employees/e1001/checklist` in the wrong case | loads. The path id is folded before the lookup, on every route |
| 11e | Search the list for `E1003` | the row appears. The id column is searchable alongside name and email |

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
| Failed comment save | Offline, save a comment | the editor **stays open with your text in it**; banner shown |
| Over-long comment | console: `App.store.setChecklistComment(id, 'laptop', 'x'.repeat(600))` | `400` with `fields.comment`. In the UI the message lands **under the box**, not in the banner, and the draft is kept |
| Failed delete | Offline, delete a row | banner; the row is still there and the page still works |
| Stale delete | delete a row in one tab, then delete the same row in a second tab | 200 both times — archiving is idempotent and the second call keeps the first stamp |
| Duplicate email | add a new employee using a seeded person's address | `409` under the email input; nothing is created — the list count is unchanged |
| Impossible date | console: `App.store.createEmployee({...VALID, startDate:'2026-02-30'})` | rejects with `status: 400` and `fields.startDate` |
| Empty table | `py scripts/seed_employees.py --wipe`, then open Add Employee | department and employment type render as **text inputs**, not empty dropdowns. Typing `Engineering` / `Full-time` creates the first hire; typing `Marketing` comes back as a `400` under the input |

## Keyboard and screen reader

Tab only — no mouse — from a fresh load of `#/employees`.

| # | Do this | Expect |
|---|---|---|
| 1 | Load any view | Focus is on the view's `<h1>`, and the tab title names the route. The first Tab lands inside the new content, not back at the browser chrome |
| 2 | Tab through the list | Every row action announces the person: "Edit Priya Sharma", not "Edit". The actions column has a name |
| 3 | Type in search | The record count is announced as it changes, without cutting off what is being read |
| 4 | Open a checklist, tick a box with Space | Focus stays **on that checkbox** after the repaint, so the next Space ticks the next item. The live region reads "Laptop issued ticked. 6 of 8 complete. In Progress." |
| 5 | Go offline, tick a box | The box snaps back, focus is **still on it** (disabling a focused element normally dumps you on `<body>`), and the live region says it was left as it was |
| 6 | Submit the form blank | Focus moves to the first bad field, which reads its own error via `aria-describedby` and reports itself as invalid |
| 7 | Press Escape with a banner showing | The banner is dismissed |
| 8 | Click anything with the mouse | **No focus ring.** It is `:focus-visible`, so rings are for keyboard users only |

Automated equivalents don't exist here — there's no JS test runner — but headless Chrome will report
`document.activeElement` after a click if you need to re-check step 4 or 5 without a screen reader.

## The one signal that matters most

Filter the console to **Errors** and run everything above, failure drills included. You should
finish with **zero `Uncaught (in promise)` warnings**.

Before Phase 3 there were six call sites that could produce them. The only console output on a
failure now should be the single deliberate `console.error` from `showError` in `app.js`, which
exists so the stack and `error.cause` stay reachable in devtools rather than being shown to an HR
user.

## Cross-check

```bash
export BASE_URL=https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev

# No token: 401, before any handler runs.
curl -s -o /dev/null -w '%{http_code}\n' $BASE_URL/employees

export TOKEN=$(curl -s -X POST $BASE_URL/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"hr.admin","password":"onboard-2026"}' \
  | py -c "import json,sys; print(json.load(sys.stdin)['token'])")

curl -s $BASE_URL/employees -H "Authorization: Bearer $TOKEN"
```

The browser and the API should agree. If they don't, the browser is caching — hard-refresh.

One more worth running by hand, because it is the claim the whole feature rests on: take an
employee token, base64-decode the middle segment, change `"role":"employee"` to
`"official"`, re-encode it and send it. **401.** The signature does not cover the payload
you just wrote.

And the revocation check, since it is the only one there is. Note a working token, rotate the
signing key (the one-liner in [README](../README.md#signing-in)), wait a minute for the authorizer
cache to turn over, and send the same token again. **401.**
