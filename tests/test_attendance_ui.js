"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

global.window = { App: {} };
require(path.join(__dirname, "..", "js", "ui.js"));

const ui = global.window.App.ui;

function row(status) {
  return {
    employeeId: "E1001",
    employeeName: "Priya Sharma",
    employeeRole: "Software Engineer",
    department: "Engineering",
    days: [{ date: "2026-09-04", status, note: "", stored: status !== null }],
    totals: { present: status === "present" ? 1 : 0, leave: status === "leave" ? 1 : 0 }
  };
}

function profile() {
  return {
    id: "E1001",
    firstName: "Priya",
    lastName: "Sharma",
    email: "priya@example.com",
    phone: "",
    personalEmail: "",
    address: "",
    department: "Engineering",
    jobTitle: "Software Engineer",
    manager: "Santosh Kumar",
    startDate: "2026-07-06",
    employmentType: "Full-time",
    status: "In progress",
    archived: false,
    checklist: [],
    progress: { done: 0, total: 0, percent: 0 }
  };
}

function ownAttendance(status, isOpen = true) {
  return {
    month: "2026-09",
    timezone: "Asia/Kolkata",
    days: ["2026-09-04"],
    employee: row(status),
    window: {
      timezone: "Asia/Kolkata",
      opensAt: "08:30",
      closesAt: "18:00",
      today: "2026-09-04",
      isOpen
    }
  };
}

test("HR sheet renders one binary checkbox per employee and date", () => {
  const html = ui.attendanceSheetView({
    month: "2026-09",
    timezone: "Asia/Kolkata",
    days: ["2026-09-04"],
    employees: [row("present")],
    count: 1
  });

  assert.match(html, /type="checkbox"/);
  assert.match(html, /data-action="edit-attendance"/);
  assert.match(html, /data-employee-id="E1001"/);
  assert.match(html, /data-date="2026-09-04"/);
  assert.match(html, /Checked = present\. Unchecked = leave\./);
  assert.doesNotMatch(html, /<select[^>]+data-action="edit-attendance"/);
  assert.equal((html.match(/data-action="edit-attendance"/g) || []).length, 1);
});

test("employee profile renders only the attendance checkbox", () => {
  const html = ui.profileView(profile(), null, ownAttendance("present"));
  const form = html.match(/<form id="attendance-form"[\s\S]*?<\/form>/)[0];

  assert.match(form, /id="attendance-present"/);
  assert.match(form, /type="checkbox" checked/);
  assert.match(form, /Present today/);
  assert.doesNotMatch(form, /<select|attendance-note|type="submit"/);
});

test("leave is represented by an unchecked employee checkbox", () => {
  const html = ui.profileView(profile(), null, ownAttendance("leave"));
  const checkbox = html.match(/<input class="attendance-checkbox"[^>]+>/)[0];
  assert.doesNotMatch(checkbox, / checked/);
});

test("employee checkbox is replaced by the window message when editing is closed", () => {
  const html = ui.profileView(profile(), null, ownAttendance("leave", false));
  assert.doesNotMatch(html, /id="attendance-form"/);
  assert.match(html, /8:30 AM to 6:00 PM Asia\/Kolkata/);
});

test("attendance saves reconcile in place instead of repainting either dashboard", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
  const hrSave = source.slice(
    source.indexOf("store.updateEmployeeAttendance("),
    source.indexOf("downloadButton.addEventListener", source.indexOf("store.updateEmployeeAttendance("))
  );
  const ownSave = source.slice(
    source.indexOf("store.markOwnAttendance("),
    source.indexOf("/* ------------------------------------------------------------- list view */")
  );

  assert.match(hrSave, /result\.attendance\.status === 'present'/);
  assert.doesNotMatch(hrSave, /renderAttendanceSheet\(\)/);
  assert.match(ownSave, /result\.attendance\.status === 'present'/);
  assert.doesNotMatch(ownSave, /renderProfile\(\)/);
});

test("attendance success uses a floating three-second toast", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "js", "app.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "..", "css", "styles.css"), "utf8");

  assert.match(app, /setTimeout\(clearNotice, 3000\)/);
  assert.match(css, /#app-notice\s*\{[\s\S]*?position:\s*fixed;/);
  assert.match(css, /#app-notice\s*\{[\s\S]*?pointer-events:\s*none;/);
});
