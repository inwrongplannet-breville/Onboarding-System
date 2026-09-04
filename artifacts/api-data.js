(() => {
  const employeeBody = {
    employeeId: "E1024",
    firstName: "Priya",
    lastName: "Sharma",
    email: "priya.sharma@breville.com",
    phone: "+61 412 883 016",
    department: "Engineering",
    jobTitle: "Software Engineer",
    manager: "Santosh Kumar",
    startDate: "2026-07-06",
    employmentType: "Full-time"
  };

  const updateBody = { ...employeeBody };
  delete updateBody.employeeId;

  const checklistResponse = [
    { id: "offer-letter", label: "Offer letter signed", owner: "HR", done: false, comment: "" },
    { id: "id-proof", label: "ID proof submitted", owner: "Employee", done: false, comment: "" },
    { id: "bank-details", label: "Bank details collected", owner: "Employee", done: false, comment: "" },
    { id: "laptop", label: "Laptop issued", owner: "IT", done: false, comment: "" },
    { id: "email-account", label: "Email / AD account created", owner: "IT", done: false, comment: "" },
    { id: "access-card", label: "Building access card issued", owner: "Operations", done: false, comment: "" },
    { id: "induction", label: "Induction session attended", owner: "HR", done: false, comment: "" },
    { id: "policy-ack", label: "Policy acknowledgement signed", owner: "Employee", done: false, comment: "" }
  ];

  const employeeResponse = {
    id: "E1024",
    firstName: "Priya",
    lastName: "Sharma",
    email: "priya.sharma@breville.com",
    phone: "+61 412 883 016",
    department: "Engineering",
    jobTitle: "Software Engineer",
    manager: "Santosh Kumar",
    startDate: "2026-07-06",
    employmentType: "Full-time",
    personalEmail: "",
    address: "",
    checklist: checklistResponse,
    status: "Pending",
    progress: { done: 0, total: 8, percent: 0 },
    archived: false,
    archivedAs: "",
    archivedAt: ""
  };

  const pathId = { name: "id", in: "path", required: true, example: "E1001", description: "Employee number; trimmed and upper-cased." };
  const managerId = { name: "id", in: "path", required: true, example: "E1001", description: "Reporting manager's employee number." };
  const okEmployee = { 200: "Employee object", 400: "ValidationError", 403: "Forbidden", 404: "NotFound", 409: "Conflict", 500: "InternalError" };
  const official = "Official token";
  const self = "Employee (self)";

  const endpoints = [
    {
      id: "post-login", tag: "Authentication", method: "POST", path: "/login", summary: "Create an 8-hour session", access: "Public", success: 200,
      description: "Checks a named official account or an employee-number login against PBKDF2 hashes in Secrets Manager, then signs a role-bearing JWT. Employee login validates the ID shape but deliberately does not check DynamoDB.",
      flow: ["API Gateway throttle (5/s)", "Login Lambda", "Secrets Manager", "Sign JWT"],
      body: { username: "hr.admin", password: "onboard-2026" },
      responses: { 200: "Token, role, displayName, expiresIn", 400: "Missing fields", 401: "Incorrect credentials", 429: "Gateway throttle", 500: "InternalError" },
      example: { token: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...", role: "official", displayName: "HR Admin", expiresIn: 28800 }
    },
    {
      id: "get-employees", tag: "Employees", method: "GET", path: "/employees", summary: "List active onboarding records", access: official, success: 200,
      description: "Returns every non-archived onboarding record with its full checklist, sorted by start date and last name. This is a paginated DynamoDB Scan because the request has no partition key.",
      flow: ["Authorizer", "Official role gate", "OnboardingTable Scan", "Drop archived + sort"],
      responses: { 200: "{ employees, count }", 401: "Unauthorized", 403: "Employee role refused", 500: "InternalError" },
      example: { employees: [employeeResponse], count: 1 }
    },
    {
      id: "post-employees", tag: "Employees", method: "POST", path: "/employees", summary: "Create an onboarding record", access: official, success: 201, mutates: true,
      description: "Creates one DynamoDB item containing the profile and eight unchecked checklist entries. A conditional PutItem makes the employee number unique within the onboarding table; work email is not unique.",
      flow: ["Authorizer", "Official role gate", "Validate + normalize", "Conditional PutItem"],
      body: employeeBody,
      responses: { 201: "Created employee + Location", 400: "Invalid employee fields", 401: "Unauthorized", 403: "Forbidden", 409: "Employee ID exists", 500: "InternalError" },
      example: employeeResponse
    },
    {
      id: "get-employee", tag: "Employees", method: "GET", path: "/employees/{id}", summary: "Get a record from either lifecycle table", access: "Official or self", success: 200,
      description: "Looks in OnboardingTable first and then EmployeeTable, so the URL survives promotion. Officials receive the full record. Employees may read only their own record and do not receive HR checklist comments.",
      flow: ["Authorizer", "Role/self scope", "Onboarding GetItem", "Employee GetItem fallback", "Role-specific view"],
      params: [pathId], responses: okEmployee, example: employeeResponse
    },
    {
      id: "put-employee", tag: "Employees", method: "PUT", path: "/employees/{id}", summary: "Replace HR-owned profile fields", access: official, success: 200, mutates: true,
      description: "Replaces the nine editable profile fields on an active onboarding record. The immutable employee ID, checklist, personal email, address, and archive state are excluded by the write whitelist.",
      flow: ["Authorizer", "Official role gate", "Validate full profile", "Guarded UpdateItem", "Consistent re-read"],
      params: [pathId], body: updateBody, responses: okEmployee, example: employeeResponse
    },
    {
      id: "delete-employee", tag: "Employees", method: "DELETE", path: "/employees/{id}", summary: "Archive an onboarding record", access: official, success: 200, mutates: true, danger: true,
      description: "Soft-deletes by stamping Onboarded or Onboarding Cancelled based on checklist completion. The item and its documents remain; subsequent onboarding writes are frozen. Repeating the request is idempotent.",
      flow: ["Authorizer", "Official role gate", "Consistent GetItem", "Compute archive state", "Guarded UpdateItem", "Consistent re-read"],
      params: [pathId], responses: { 200: "Archived employee", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 500: "InternalError" },
      example: { ...employeeResponse, archived: true, archivedAs: "Onboarding Cancelled", archivedAt: "2026-09-04T10:30:00Z" }
    },
    {
      id: "patch-contact", tag: "Employees", method: "PATCH", path: "/employees/{id}/contact", summary: "Update the signed-in employee's contact details", access: self, success: 200, mutates: true,
      description: "The employee-only write path. Any subset of phone, personalEmail, and address may be supplied; null or an empty string clears a field. Officials do not bypass the self check.",
      flow: ["Authorizer", "Exact self check", "Find lifecycle table", "Guarded UpdateItem", "Trim comments from response"],
      params: [pathId], body: { phone: "+61 400 000 000", personalEmail: "priya@example.com", address: "12 Smith Street, Sydney NSW 2000" },
      responses: okEmployee, example: { ...employeeResponse, personalEmail: "priya@example.com", address: "12 Smith Street, Sydney NSW 2000" }
    },
    {
      id: "get-documents", tag: "Documents", method: "GET", path: "/employees/{id}/documents", summary: "Inspect all three document slots", access: "Official or self", success: 200,
      description: "Heads the three fixed S3 keys and returns every slot, including empty ones. Uploaded slots contain a five-minute presigned download URL. This Lambda has no DynamoDB access.",
      flow: ["Authorizer", "Role/self scope", "3× S3 HeadObject", "Presign downloads"],
      params: [pathId], responses: { 200: "{ documents: [3 slots] }", 401: "Unauthorized", 403: "Forbidden", 500: "InternalError" },
      example: { documents: [{ slot: "resume", label: "Resume", uploaded: false }, { slot: "id-document", label: "ID document", uploaded: true, filename: "Passport.pdf", contentType: "application/pdf", size: 248031, uploadedAt: "2026-09-04T10:30:00Z", downloadUrl: "https://..." }, { slot: "signed-offer-letter", label: "Signed offer letter", uploaded: false }] }
    },
    {
      id: "post-document-upload", tag: "Documents", method: "POST", path: "/employees/{id}/documents/{slot}", summary: "Request a direct-to-S3 upload ticket", access: self, success: 200, mutates: true,
      description: "Validates the employee and file metadata, then returns a five-minute presigned S3 POST. This API call does not receive the file; the browser must submit the returned fields plus the file directly to the returned URL (maximum 10 MB).",
      flow: ["Authorizer", "Exact self check", "Record exists + active", "Presign S3 POST", "Browser uploads to S3"],
      params: [pathId, { name: "slot", in: "path", required: true, example: "resume", description: "resume | id-document | signed-offer-letter" }],
      body: { filename: "Priya-Sharma-Resume.pdf", contentType: "application/pdf" },
      responses: { 200: "Presigned URL and form fields", 400: "Bad slot/type/name", 401: "Unauthorized", 403: "Not self", 404: "Employee missing", 409: "Archived", 500: "InternalError" },
      example: { slot: "resume", filename: "Priya-Sharma-Resume.pdf", url: "https://bucket.s3.eu-north-1.amazonaws.com/", fields: { key: "employees/E1024/resume", "Content-Type": "application/pdf", policy: "...", "x-amz-signature": "..." } }
    },
    {
      id: "patch-checklist", tag: "Checklist", method: "PATCH", path: "/employees/{id}/checklist/{itemId}", summary: "Set a checklist tick or HR comment", access: official, success: 200, mutates: true,
      description: "Partially updates done, comment, or both on one of eight fixed checklist items. DynamoDB updates nested paths server-side, preventing concurrent ticks/comments from overwriting one another. Archived records are frozen.",
      flow: ["Authorizer", "Official role gate", "Validate item/body", "Nested UpdateItem + guards", "Consistent re-read"],
      params: [pathId, { name: "itemId", in: "path", required: true, example: "offer-letter", description: "One of the eight checklist IDs." }],
      body: { done: true, comment: "Signed copy received." }, responses: okEmployee, example: { ...employeeResponse, checklist: checklistResponse.map((item, index) => index === 0 ? { ...item, done: true, comment: "Signed copy received." } : item), status: "In Progress", progress: { done: 1, total: 8, percent: 13 } }
    },
    {
      id: "get-staff-employees", tag: "Staff", method: "GET", path: "/staff/employees", summary: "List promoted employees", access: official, success: 200,
      description: "Scans EmployeeTable for entityType Employee and sorts by joinedOn and last name. The response preserves onboarding checklist history and adds onboardedAt, joinedOn, and intern IDs.",
      flow: ["Authorizer", "Official role gate", "EmployeeTable Scan", "Filter employees + sort"],
      responses: { 200: "{ employees, count }", 401: "Unauthorized", 403: "Forbidden", 500: "InternalError" },
      example: { employees: [{ ...employeeResponse, onboardedAt: "2026-08-28T11:00:00Z", joinedOn: "2026-07-06", interns: ["E1042"] }], count: 1 }
    },
    {
      id: "post-staff-employees", tag: "Staff", method: "POST", path: "/staff/employees", summary: "Copy a completed hire to the staff dashboard", access: official, success: 201, mutates: true,
      description: "Promotion step 1 for a non-intern. Requires an eight-of-eight checklist, copies the record into EmployeeTable, and preserves the checklist as frozen history. Call DELETE /onboarding/{id} only after this succeeds.",
      flow: ["Authorizer", "Official role gate", "Read completed onboarding", "Conditional PutItem in EmployeeTable"],
      body: { employeeId: "E1024" },
      responses: { 201: "Promoted employee", 400: "Wrong employment type", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 409: "Incomplete/already promoted", 500: "InternalError" },
      example: { ...employeeResponse, status: "Onboarded", onboardedAt: "2026-09-04T10:30:00Z", joinedOn: "2026-07-06", interns: [] }
    },
    {
      id: "delete-staff-employee", tag: "Staff", method: "DELETE", path: "/staff/employees/{id}", summary: "Remove a promoted employee after restore", access: official, success: 200, mutates: true, danger: true,
      description: "The destructive last step of undo promotion for a non-intern. It works only within seven days and only after POST /onboarding/restore has recreated the onboarding copy. Missing is idempotent success.",
      flow: ["Authorizer", "Official role gate", "Check staff kind + 7-day window", "Require onboarding copy", "DeleteItem"],
      params: [pathId], responses: { 200: "{ id }", 401: "Unauthorized", 403: "Forbidden", 404: "Wrong staff kind", 409: "Not restored/window closed", 500: "InternalError" }, example: { id: "E1024" }
    },
    {
      id: "post-manager-intern", tag: "Staff", method: "POST", path: "/staff/employees/{id}/interns", summary: "Link an intern to a manager", access: official, success: 200, mutates: true,
      description: "Adds one intern ID to a promoted manager's sparse interns list. A conditional list append prevents duplicates under concurrency. Repeating an existing link is a successful no-op.",
      flow: ["Authorizer", "Official role gate", "Verify intern kind", "Conditional list append"],
      params: [managerId], body: { internId: "E1042" },
      responses: { 200: "Updated manager", 400: "Intern missing/wrong kind", 401: "Unauthorized", 403: "Forbidden", 404: "Manager missing", 500: "InternalError" },
      example: { ...employeeResponse, onboardedAt: "2026-08-28T11:00:00Z", joinedOn: "2026-07-06", interns: ["E1042"] }
    },
    {
      id: "delete-manager-intern", tag: "Staff", method: "DELETE", path: "/staff/employees/{id}/interns/{internId}", summary: "Unlink an intern from a manager", access: official, success: 200, mutates: true, danger: true,
      description: "Removes an intern ID from a manager's list; removes the whole sparse attribute when the list becomes empty. Missing links are idempotent success. It does not change the intern's reportingManagerId.",
      flow: ["Authorizer", "Official role gate", "Read manager list", "Remove list index/attribute"],
      params: [managerId, { name: "internId", in: "path", required: true, example: "E1042", description: "Intern employee number." }],
      responses: { 200: "Updated manager", 401: "Unauthorized", 403: "Forbidden", 404: "Manager missing", 500: "InternalError" },
      example: { ...employeeResponse, onboardedAt: "2026-08-28T11:00:00Z", joinedOn: "2026-07-06", interns: [] }
    },
    {
      id: "get-staff-interns", tag: "Staff", method: "GET", path: "/staff/interns", summary: "List interns, optionally by manager", access: official, success: 200,
      description: "Without managerId, scans EmployeeTable for interns. With managerId, queries the sparse ByReportingManager GSI and returns only that manager's interns. Results are sorted by joinedOn and last name.",
      flow: ["Authorizer", "Official role gate", "Scan or GSI Query", "Map interns + sort"],
      params: [{ name: "managerId", in: "query", required: false, example: "", description: "Optional reporting manager employee number." }],
      responses: { 200: "{ interns, count }", 401: "Unauthorized", 403: "Forbidden", 500: "InternalError" },
      example: { interns: [{ ...employeeResponse, employmentType: "Intern", onboardedAt: "2026-09-01T09:00:00Z", joinedOn: "2026-07-06", reportingManagerId: "E1001" }], count: 1 }
    },
    {
      id: "post-staff-interns", tag: "Staff", method: "POST", path: "/staff/interns", summary: "Copy a completed intern to the staff dashboard", access: official, success: 201, mutates: true,
      description: "Promotion step 1 for an intern. Requires a completed checklist and an existing promoted employee as manager. Then link the intern to that manager and delete the onboarding copy, in that order.",
      flow: ["Authorizer", "Official role gate", "Read completed intern", "Verify manager employee", "Conditional PutItem"],
      body: { employeeId: "E1042", reportingManagerId: "E1001" },
      responses: { 201: "Promoted intern", 400: "Wrong type/manager", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 409: "Incomplete/already promoted", 500: "InternalError" },
      example: { ...employeeResponse, id: "E1042", employmentType: "Intern", status: "Onboarded", onboardedAt: "2026-09-04T10:30:00Z", joinedOn: "2026-07-06", reportingManagerId: "E1001" }
    },
    {
      id: "put-intern-manager", tag: "Staff", method: "PUT", path: "/staff/interns/{id}/manager", summary: "Set an intern's reporting manager", access: official, success: 200, mutates: true,
      description: "Reassignment step 1. Updates the intern row and returns previousReportingManagerId so the caller can add the new manager link before removing the old one. Assigning the current manager is an idempotent no-op.",
      flow: ["Authorizer", "Official role gate", "Verify intern + new manager", "UpdateItem", "Return previous manager"],
      params: [pathId], body: { reportingManagerId: "E1002" },
      responses: { 200: "Updated intern + previous manager", 400: "Manager invalid", 401: "Unauthorized", 403: "Forbidden", 404: "Intern missing", 500: "InternalError" },
      example: { ...employeeResponse, employmentType: "Intern", reportingManagerId: "E1002", previousReportingManagerId: "E1001" }
    },
    {
      id: "delete-staff-intern", tag: "Staff", method: "DELETE", path: "/staff/interns/{id}", summary: "Remove a promoted intern after restore", access: official, success: 200, mutates: true, danger: true,
      description: "The destructive last step of undo promotion for an intern. First restore onboarding, then unlink the manager, then call this within seven days. It refuses employee rows and does not alter manager links itself.",
      flow: ["Authorizer", "Official role gate", "Check intern kind + 7-day window", "Require onboarding copy", "DeleteItem"],
      params: [pathId], responses: { 200: "{ id }", 401: "Unauthorized", 403: "Forbidden", 404: "Wrong staff kind", 409: "Not restored/window closed", 500: "InternalError" }, example: { id: "E1042" }
    },
    {
      id: "delete-onboarding", tag: "Lifecycle", method: "DELETE", path: "/onboarding/{id}", summary: "Remove the onboarding copy after promotion", access: official, success: 200, mutates: true, danger: true,
      description: "The destructive last promotion step. It refuses to delete until the same ID exists in EmployeeTable, ensuring an interrupted workflow leaves two copies rather than none. Missing onboarding rows are idempotent success.",
      flow: ["Authorizer", "Official role gate", "Require staff copy", "Delete onboarding item"],
      params: [pathId], responses: { 200: "{ id, movedTo }", 401: "Unauthorized", 403: "Forbidden", 409: "Not promoted first", 500: "InternalError" }, example: { id: "E1024", movedTo: "employee" }
    },
    {
      id: "post-onboarding-restore", tag: "Lifecycle", method: "POST", path: "/onboarding/restore", summary: "Restore a recently promoted record", access: official, success: 201, mutates: true,
      description: "Undo-promotion step 1. Within seven days of onboardedAt, copies a staff record and its full checklist history back into OnboardingTable. Delete the staff copy only after this succeeds.",
      flow: ["Authorizer", "Official role gate", "Read staff record", "Check 7-day window", "Conditional PutItem"],
      body: { employeeId: "E1024" },
      responses: { 201: "Restored onboarding employee", 400: "ID required", 401: "Unauthorized", 403: "Forbidden", 404: "Staff record missing", 409: "Window closed/already restored", 500: "InternalError" },
      example: employeeResponse
    }
  ];

  const models = {
    employee: employeeResponse,
    error: { error: { code: "ValidationError", message: "Employee details are not valid.", fields: { email: "Enter a valid email address." } } },
    document: { slot: "id-document", label: "ID document", uploaded: true, filename: "Passport.pdf", contentType: "application/pdf", size: 248031, uploadedAt: "2026-09-04T10:30:00Z", downloadUrl: "https://...five-minute-presigned-url..." }
  };

  const functionFlows = [
    {
      category: "Access & dashboards", title: "Sign in", actor: "Everyone",
      description: "Exchange the selected official or employee credentials for a bearer token.",
      steps: [{ calls: [{ method: "POST", path: "/login", note: "Token returned" }] }]
    },
    {
      category: "Access & dashboards", title: "Open HR home", actor: "Official",
      description: "The landing screen is only navigation; it does not load a report.",
      steps: [{ local: "No endpoint · render static navigation" }]
    },
    {
      category: "Access & dashboards", title: "Open onboarding dashboard", actor: "Official",
      description: "Load all active onboarding records and their embedded checklists.",
      steps: [{ calls: [{ method: "GET", path: "/employees", note: "List + facets" }] }]
    },
    {
      category: "Access & dashboards", title: "Search or filter onboarding", actor: "Official",
      description: "Search, department, and status filters run over the already-loaded list.",
      steps: [{ local: "No endpoint · filter the in-memory GET /employees result" }]
    },
    {
      category: "Access & dashboards", title: "Open employee tracking", actor: "Official",
      description: "Load promoted, non-intern staff for the employee tracking screen.",
      steps: [{ calls: [{ method: "GET", path: "/staff/employees", note: "Promoted employees" }] }]
    },
    {
      category: "Access & dashboards", title: "Open interns dashboard", actor: "Official",
      description: "Intern cards and the manager choices are fetched together.",
      steps: [{ parallel: true, calls: [{ method: "GET", path: "/staff/interns", note: "Intern cards" }, { method: "GET", path: "/staff/employees", note: "Manager picker" }] }]
    },
    {
      category: "Onboarding records", title: "Open Add Employee form", actor: "Official",
      description: "The directory is loaded only when its cached copy is unavailable; it supplies form facets.",
      steps: [{ optional: true, calls: [{ method: "GET", path: "/employees", note: "If list cache is empty" }] }]
    },
    {
      category: "Onboarding records", title: "Create employee", actor: "Official",
      description: "Create the record, navigate back, then refresh the onboarding list.",
      steps: [{ calls: [{ method: "POST", path: "/employees", note: "Create" }] }, { calls: [{ method: "GET", path: "/employees", note: "Refresh dashboard" }] }]
    },
    {
      category: "Onboarding records", title: "Open Edit Employee form", actor: "Official",
      description: "Load the selected record; the directory request occurs alongside it only when the cache is empty.",
      steps: [{ parallel: true, calls: [{ method: "GET", path: "/employees/{id}", note: "Selected record" }, { method: "GET", path: "/employees", note: "Optional · empty cache" }] }]
    },
    {
      category: "Onboarding records", title: "Save employee changes", actor: "Official",
      description: "Replace all HR-owned fields on an onboarding record, then refresh the list.",
      steps: [{ calls: [{ method: "PUT", path: "/employees/{id}", note: "Full profile replace" }] }, { calls: [{ method: "GET", path: "/employees", note: "Refresh dashboard" }] }]
    },
    {
      category: "Onboarding records", title: "Archive from onboarding list", actor: "Official",
      description: "Stamp the record as cancelled/onboarded, remove it from the active list, then reload that list.",
      steps: [{ calls: [{ method: "DELETE", path: "/employees/{id}", note: "Archive, not hard-delete" }] }, { calls: [{ method: "GET", path: "/employees", note: "Refresh dashboard" }] }]
    },
    {
      category: "Onboarding records", title: "Open checklist and documents", actor: "Official",
      description: "The employee and document slots load independently. A completed intern additionally needs managers for its promotion picker.",
      steps: [{ parallel: true, calls: [{ method: "GET", path: "/employees/{id}", note: "Checklist/profile" }, { method: "GET", path: "/employees/{id}/documents", note: "Document slots" }] }, { optional: true, calls: [{ method: "GET", path: "/staff/employees", note: "Completed intern only" }] }]
    },
    {
      category: "Onboarding records", title: "Tick or untick checklist item", actor: "Official",
      description: "The PATCH response already contains recomputed progress, so no follow-up GET is needed.",
      steps: [{ calls: [{ method: "PATCH", path: "/employees/{id}/checklist/{itemId}", note: "Body: done" }] }]
    },
    {
      category: "Onboarding records", title: "Save checklist comment", actor: "Official",
      description: "Comments share the checklist PATCH; saving text leaves the tick unchanged.",
      steps: [{ calls: [{ method: "PATCH", path: "/employees/{id}/checklist/{itemId}", note: "Body: comment" }] }]
    },
    {
      category: "Onboarding records", title: "Remove checklist comment", actor: "Official",
      description: "The same small endpoint receives an empty comment and removes only that nested attribute.",
      steps: [{ calls: [{ method: "PATCH", path: "/employees/{id}/checklist/{itemId}", note: "Body: comment = empty" }] }]
    },
    {
      category: "Staff lifecycle", title: "Promote completed employee", actor: "Official",
      description: "Copy first, delete the onboarding copy second, then refresh the destination list.",
      steps: [{ calls: [{ method: "POST", path: "/staff/employees", note: "Copy to staff" }] }, { calls: [{ method: "DELETE", path: "/onboarding/{id}", note: "Remove old copy" }] }, { calls: [{ method: "GET", path: "/employees", note: "Return to onboarding" }] }]
    },
    {
      category: "Staff lifecycle", title: "Promote completed intern", actor: "Official",
      description: "Create the intern, link it to the selected manager, remove onboarding last, then return to the list.",
      steps: [{ calls: [{ method: "POST", path: "/staff/interns", note: "Copy + manager ID" }] }, { calls: [{ method: "POST", path: "/staff/employees/{managerId}/interns", note: "Add manager link" }] }, { calls: [{ method: "DELETE", path: "/onboarding/{id}", note: "Remove old copy" }] }, { calls: [{ method: "GET", path: "/employees", note: "Return to onboarding" }] }]
    },
    {
      category: "Staff lifecycle", title: "Reassign intern manager", actor: "Official",
      description: "Write the new source-of-truth manager, add its link, remove the old link if different, then repaint both dashboard datasets.",
      steps: [{ calls: [{ method: "PUT", path: "/staff/interns/{id}/manager", note: "Returns previous manager" }] }, { calls: [{ method: "POST", path: "/staff/employees/{newManagerId}/interns", note: "Add new link" }] }, { optional: true, calls: [{ method: "DELETE", path: "/staff/employees/{oldManagerId}/interns/{internId}", note: "If manager changed" }] }, { parallel: true, calls: [{ method: "GET", path: "/staff/interns", note: "Refresh interns" }, { method: "GET", path: "/staff/employees", note: "Refresh managers" }] }]
    },
    {
      category: "Staff lifecycle", title: "Undo employee promotion", actor: "Official",
      description: "Restore the durable onboarding copy before removing the staff copy; available for seven days.",
      steps: [{ calls: [{ method: "POST", path: "/onboarding/restore", note: "Restore first" }] }, { calls: [{ method: "DELETE", path: "/staff/employees/{id}", note: "Remove staff copy" }] }, { calls: [{ method: "GET", path: "/staff/employees", note: "Refresh tracking" }] }]
    },
    {
      category: "Staff lifecycle", title: "Undo intern promotion", actor: "Official",
      description: "Restore first, unlink the manager, remove the intern copy, then refresh both intern-screen datasets.",
      steps: [{ calls: [{ method: "POST", path: "/onboarding/restore", note: "Restore first" }] }, { calls: [{ method: "DELETE", path: "/staff/employees/{managerId}/interns/{internId}", note: "Remove manager link" }] }, { calls: [{ method: "DELETE", path: "/staff/interns/{id}", note: "Remove staff copy" }] }, { parallel: true, calls: [{ method: "GET", path: "/staff/interns", note: "Refresh interns" }, { method: "GET", path: "/staff/employees", note: "Refresh managers" }] }]
    },
    {
      category: "Employee self-service", title: "Open My Profile", actor: "Employee",
      description: "The own record and document slots load concurrently using the employee ID from the token.",
      steps: [{ parallel: true, calls: [{ method: "GET", path: "/employees/{ownId}", note: "Own profile" }, { method: "GET", path: "/employees/{ownId}/documents", note: "Own documents" }] }]
    },
    {
      category: "Employee self-service", title: "Update own contact details", actor: "Employee",
      description: "Patch phone, personal email, and address; the response is repainted directly.",
      steps: [{ calls: [{ method: "PATCH", path: "/employees/{ownId}/contact", note: "No follow-up GET" }] }]
    },
    {
      category: "Employee self-service", title: "Upload a document", actor: "Employee",
      description: "Request a five-minute ticket, upload bytes straight to S3, then refresh all three slots.",
      steps: [{ calls: [{ method: "POST", path: "/employees/{ownId}/documents/{slot}", note: "Get ticket" }] }, { calls: [{ method: "POST", path: "{presigned S3 URL}", note: "Direct multipart upload", external: true }] }, { calls: [{ method: "GET", path: "/employees/{ownId}/documents", note: "Refresh slots" }] }]
    },
    {
      category: "Employee self-service", title: "Download a document", actor: "Official or employee",
      description: "Load slots when not cached, then navigate directly to the five-minute S3 download URL.",
      steps: [{ optional: true, calls: [{ method: "GET", path: "/employees/{id}/documents", note: "If slots are not cached" }] }, { calls: [{ method: "GET", path: "{presigned S3 URL}", note: "Direct download", external: true }] }]
    },
    {
      category: "Small supporting actions", title: "List interns for one manager", actor: "Official",
      description: "The optional managerId query switches the backend from a Scan to the reporting-manager index.",
      steps: [{ calls: [{ method: "GET", path: "/staff/interns?managerId={id}", note: "GSI query" }] }]
    },
    {
      category: "Small supporting actions", title: "Sign out", actor: "Everyone",
      description: "Clear the token and identity from sessionStorage; AWS is not contacted.",
      steps: [{ local: "No endpoint · clear browser session" }]
    },
    {
      category: "Small supporting actions", title: "Expire an invalid session", actor: "Everyone",
      description: "Any authenticated request returning 401 clears the local session and routes back to login.",
      steps: [{ local: "Any API request returns 401" }, { local: "No endpoint · clear browser session" }]
    }
  ];

  const storeFunctions = [
    ["login", "POST /login"],
    ["listEmployees", "GET /employees"],
    ["getEmployee", "GET /employees/{id}"],
    ["createEmployee", "POST /employees"],
    ["updateEmployee", "PUT /employees/{id}"],
    ["archiveEmployee", "DELETE /employees/{id}"],
    ["getOwnProfile", "GET /employees/{ownId}"],
    ["updateOwnContact", "PATCH /employees/{ownId}/contact"],
    ["getDocuments", "GET /employees/{id}/documents"],
    ["getOwnDocuments", "GET /employees/{ownId}/documents"],
    ["requestUpload", "POST /employees/{id}/documents/{slot}"],
    ["uploadToS3", "POST {presigned S3 URL}"],
    ["promoteToEmployee", "POST /staff/employees"],
    ["promoteToIntern", "POST /staff/interns"],
    ["addManagerIntern", "POST /staff/employees/{managerId}/interns"],
    ["listStaffEmployees", "GET /staff/employees"],
    ["listInterns", "GET /staff/interns[?managerId={id}]"],
    ["removeManagerIntern", "DELETE /staff/employees/{managerId}/interns/{internId}"],
    ["setInternManager", "PUT /staff/interns/{id}/manager"],
    ["deleteOnboardingRecord", "DELETE /onboarding/{id}"],
    ["restoreOnboarding", "POST /onboarding/restore"],
    ["deleteStaffEmployee", "DELETE /staff/employees/{id}"],
    ["deleteStaffIntern", "DELETE /staff/interns/{id}"],
    ["promote", "promoteToEmployee|promoteToIntern → [addManagerIntern] → deleteOnboardingRecord"],
    ["unpromote", "restoreOnboarding → [removeManagerIntern] → deleteStaffEmployee|deleteStaffIntern"],
    ["reassignManager", "setInternManager → addManagerIntern → [removeManagerIntern]"],
    ["setChecklistItem", "PATCH /employees/{id}/checklist/{itemId} · done"],
    ["setChecklistComment", "PATCH /employees/{id}/checklist/{itemId} · comment"]
  ].map(([name, sequence]) => ({ name, sequence }));

  window.API_DOCS = {
    baseUrl: "https://4w9q4450be.execute-api.eu-north-1.amazonaws.com/dev",
    credentials: {
      official: { username: "hr.admin", password: "onboard-2026" },
      employee: { username: "E1001", password: "welcome-2026" }
    },
    checklistIds: ["offer-letter", "id-proof", "bank-details", "laptop", "email-account", "access-card", "induction", "policy-ack"],
    endpoints,
    models,
    functionFlows,
    storeFunctions
  };
})();
