
## Problem Statement

Breville needs a lightweight internal tool to manage employee records and streamline the onboarding process for new hires. Currently, onboarding data (personal details, documents, onboarding checklist status) is tracked manually, leading to duplication and lack of visibility.

Your task is to design and build a simple **Employee Management & Onboarding System** — a web application where an HR/Admin user can:
- Add, view, update, and remove employee records
- Upload and store onboarding documents (offer letter, ID proof, etc.)
- Track onboarding checklist progress for each employee
- Automatically trigger onboarding-related actions (notifications, IT setup request, HR alert) when a new employee joins

You'll build this step by step — starting with a simple visual UI, then the backend on AWS, then connecting the two together. The goal is genuine hands-on AWS experience using patterns similar to what we use in production (Lambda, API Gateway, DynamoDB, S3, SNS, SQS).

---

## Task Breakdown

### Phase 1 — UI Shell (Mock Data, No Backend Yet)

**Goal:** A simple, working frontend using hardcoded/mock data — no AWS involved yet.

| #   | Task                                                 | Hint                                                                        |
| --- | ---------------------------------------------------- | --------------------------------------------------------------------------- |
| 1   | Build an employee list view                          | Hardcode a small array of sample employees in the frontend code itself      |
| 2   | Build an "Add Employee" form                         | On submit, just add to the local hardcoded list (no persistence needed yet) |
| 3   | Build an "Edit Employee" view                        | Pre-fill the form with existing mock data, update the local list on save    |
| 4   | Build a basic onboarding checklist view per employee | Simple checkboxes — state can just live in the frontend for now             |
| 5   | Review with Santosh                                  | Confirm the UI direction and layout before any backend work begins          |

**Note:** Keep this simple — plain HTML/CSS/JS or basic React. No styling polish needed at this stage; the point is structure and flow, not visual design.

---

### Phase 2 — CRUD Backend on AWS (No UI Integration Yet)

**Goal:** Build the real backend independently, tested via Postman/curl — not yet connected to the UI.

| # | Task | Hint |
|---|---|---|
| 1 | Design the employee record structure | Think about what uniquely identifies an employee — this becomes your partition key. Service: **DynamoDB** |
| 2 | Lambda: create employee | Service: **Lambda + DynamoDB** ("put item" operation) |
| 3 | Lambda: get employee by ID | Different from listing all — "get item" operation |
| 4 | Lambda: list all employees | Look up **Scan vs Query** in DynamoDB — ask him which is more appropriate here and why |
| 5 | Lambda: update employee | "update item" — different from re-inserting the whole record |
| 6 | Lambda: delete employee | "delete item" operation |
| 7 | Expose all Lambdas as REST endpoints | Service: **API Gateway** — one route per operation |
| 8 | Test everything via Postman/curl | No UI involved at this stage — isolates backend bugs from frontend bugs |

---

### Phase 3 — Integration (Connect UI to Real Backend)

**Goal:** Replace mock data in the UI with real API calls.

| # | Task | Hint |
|---|---|---|
| 1 | Replace hardcoded employee list with a live API call | Watch out for **CORS** settings on API Gateway — this trips up most people the first time |
| 2 | Wire up Add/Edit/Delete forms to real endpoints | Confirm data actually persists in DynamoDB after a page refresh |
| 3 | Wire up the onboarding checklist to real backend state | Requires extending the DynamoDB record — see Phase 4 |
| 4 | Test the full flow end-to-end | Add → Edit → Delete → Refresh → confirm persistence |

---
