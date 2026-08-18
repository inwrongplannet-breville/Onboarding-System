
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

