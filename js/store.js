/**
 * Data access layer - the seam between the UI and wherever employees live.
 *
 * Today that's an in-memory array seeded from data.js. In Phase 3 the bodies of
 * these functions become fetch() calls to API Gateway; every function is already
 * async and every caller already awaits, so nothing outside this file changes.
 *
 * Callers get deep copies, never live references, so the only way to change
 * stored data is through the mutator functions here - same contract a real API
 * would give us.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  var employees = App.seedEmployees();
  var nextIdSeq = employees.length + 1;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function nextId() {
    var id = 'emp-' + String(nextIdSeq).padStart(3, '0');
    nextIdSeq += 1;
    return id;
  }

  function findIndex(id) {
    for (var i = 0; i < employees.length; i += 1) {
      if (employees[i].id === id) return i;
    }
    return -1;
  }

  /** Only the fields a form is allowed to set - id and checklist are ours. */
  var EDITABLE_FIELDS = [
    'firstName', 'lastName', 'email', 'phone',
    'department', 'jobTitle', 'manager', 'startDate', 'employmentType'
  ];

  function pickEditable(input) {
    var out = {};
    EDITABLE_FIELDS.forEach(function (field) {
      out[field] = typeof input[field] === 'string' ? input[field].trim() : (input[field] || '');
    });
    return out;
  }

  App.store = {
    listEmployees: function () {
      return Promise.resolve(clone(employees));
    },

    getEmployee: function (id) {
      var index = findIndex(id);
      return Promise.resolve(index === -1 ? null : clone(employees[index]));
    },

    createEmployee: function (input) {
      var employee = pickEditable(input);
      employee.id = nextId();
      employee.checklist = App.checklistTemplate();
      employees.push(employee);
      return Promise.resolve(clone(employee));
    },

    updateEmployee: function (id, input) {
      var index = findIndex(id);
      if (index === -1) return Promise.reject(new Error('Employee not found: ' + id));

      var updated = pickEditable(input);
      updated.id = id;
      updated.checklist = employees[index].checklist; // checklist is edited separately
      employees[index] = updated;
      return Promise.resolve(clone(updated));
    },

    deleteEmployee: function (id) {
      var index = findIndex(id);
      if (index === -1) return Promise.reject(new Error('Employee not found: ' + id));
      employees.splice(index, 1);
      return Promise.resolve();
    },

    setChecklistItem: function (employeeId, itemId, done) {
      var index = findIndex(employeeId);
      if (index === -1) return Promise.reject(new Error('Employee not found: ' + employeeId));

      var item = employees[index].checklist.filter(function (entry) {
        return entry.id === itemId;
      })[0];
      if (!item) return Promise.reject(new Error('Checklist item not found: ' + itemId));

      item.done = !!done;
      return Promise.resolve(clone(employees[index]));
    }
  };
})(window.App);
