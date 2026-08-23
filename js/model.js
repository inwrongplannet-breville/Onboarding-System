/**
 * Shared model: the two enums the form's dropdowns need, and four pure helpers
 * for displaying an employee.
 *
 * There are no employee records in this file, and there are none anywhere else
 * in js/ either. The UI's only source of employees is DynamoDB, via store.js.
 * That is deliberate: a leftover fixture array is how a broken API ends up
 * looking like a working one.
 *
 * The enums are duplicated in common/models.py, which validates them server
 * side. They are schema rather than data - the same two lists the DynamoDB
 * records are checked against.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  App.DEPARTMENTS = ['Engineering', 'HR', 'Finance', 'Operations'];
  App.EMPLOYMENT_TYPES = ['Full-time', 'Contract', 'Intern'];

  App.fullName = function (employee) {
    return (employee.firstName + ' ' + employee.lastName).trim();
  };

  App.progress = function (employee) {
    var items = employee.checklist || [];
    var done = items.filter(function (item) { return item.done; }).length;
    return {
      done: done,
      total: items.length,
      percent: items.length ? Math.round((done / items.length) * 100) : 0
    };
  };

  /**
   * Status is derived, never stored - so it can't drift out of sync with the
   * checklist it describes. The API sends its own `status` and `progress` on
   * every employee; we keep computing ours because the list filter in app.js
   * runs over records already in memory, and the two agree exactly.
   */
  App.computeStatus = function (employee) {
    var p = App.progress(employee);
    if (p.total === 0 || p.done === 0) return 'Pending';
    if (p.done === p.total) return 'Onboarded';
    return 'In Progress';
  };

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /** "2026-09-01" -> "1 Sep 2026". Parsed by hand to dodge timezone shifts. */
  App.formatDate = function (isoDate) {
    if (!isoDate) return '-';
    var parts = String(isoDate).split('-');
    if (parts.length !== 3) return isoDate;
    var month = MONTHS[Number(parts[1]) - 1];
    if (!month) return isoDate;
    return Number(parts[2]) + ' ' + month + ' ' + parts[0];
  };
})(window.App);
