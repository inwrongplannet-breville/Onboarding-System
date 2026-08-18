/**
 * Router, event wiring and form validation.
 *
 * Routes (hash-based, so views are linkable and the back button works):
 *   #/employees
 *   #/employees/new
 *   #/employees/:id/edit
 *   #/employees/:id/checklist
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  var root = document.getElementById('app');
  var store = App.store;
  var ui = App.ui;

  // List filters survive navigation, so returning from a checklist keeps the view.
  var filters = { search: '', department: '', status: '' };
  var loadedEmployees = [];

  /* ---------------------------------------------------------------- routing */

  function parseHash() {
    var raw = window.location.hash.replace(/^#\/?/, '');
    var segments = raw.split('/').filter(Boolean).map(decodeURIComponent);

    if (segments[0] !== 'employees') return { name: 'list' };
    if (segments.length === 1) return { name: 'list' };
    if (segments[1] === 'new') return { name: 'new' };
    if (segments.length === 3 && segments[2] === 'edit') return { name: 'edit', id: segments[1] };
    if (segments.length === 3 && segments[2] === 'checklist') return { name: 'checklist', id: segments[1] };
    return { name: 'list' };
  }

  function navigate(hash) {
    window.location.hash = hash;
  }

  function render() {
    var route = parseHash();

    if (route.name === 'list') return renderList();
    if (route.name === 'new') return renderForm(null);
    if (route.name === 'edit') return renderForm(route.id);
    if (route.name === 'checklist') return renderChecklist(route.id);
  }

  /* ------------------------------------------------------------- list view */

  function applyFilters(employees) {
    var term = filters.search.trim().toLowerCase();

    return employees.filter(function (employee) {
      if (filters.department && employee.department !== filters.department) return false;
      if (filters.status && App.computeStatus(employee) !== filters.status) return false;
      if (!term) return true;

      var haystack = (App.fullName(employee) + ' ' + employee.email).toLowerCase();
      return haystack.indexOf(term) !== -1;
    });
  }

  function refreshRows() {
    var tbody = document.getElementById('employee-rows');
    if (!tbody) return;

    var visible = applyFilters(loadedEmployees);
    tbody.innerHTML = ui.employeeRows(visible);
    document.getElementById('record-count').textContent =
      ui.countLabel(visible.length, loadedEmployees.length);
  }

  function renderList() {
    store.listEmployees().then(function (employees) {
      loadedEmployees = employees;
      root.innerHTML = ui.listView(employees, filters);
      refreshRows();

      document.getElementById('search').addEventListener('input', function (event) {
        filters.search = event.target.value;
        refreshRows();
      });

      document.getElementById('department-filter').addEventListener('change', function (event) {
        filters.department = event.target.value;
        refreshRows();
      });

      document.getElementById('status-filter').addEventListener('change', function (event) {
        filters.status = event.target.value;
        refreshRows();
      });

      document.getElementById('employee-rows').addEventListener('click', function (event) {
        var button = event.target.closest('[data-action="delete"]');
        if (!button) return;

        var row = button.closest('tr');
        var id = row.getAttribute('data-id');
        var employee = loadedEmployees.filter(function (item) { return item.id === id; })[0];
        if (!employee) return;

        if (!window.confirm('Remove ' + App.fullName(employee) + ' from the system?')) return;

        store.deleteEmployee(id).then(function () {
          renderList();
        });
      });
    });
  }

  /* ------------------------------------------------------------- form view */

  var REQUIRED_FIELDS = [
    { name: 'firstName', label: 'First name' },
    { name: 'lastName', label: 'Last name' },
    { name: 'email', label: 'Work email' },
    { name: 'department', label: 'Department' },
    { name: 'jobTitle', label: 'Job title' },
    { name: 'startDate', label: 'Start date' },
    { name: 'employmentType', label: 'Employment type' }
  ];

  var EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function readForm(form) {
    var values = {};
    Array.prototype.forEach.call(form.elements, function (element) {
      if (element.name) values[element.name] = element.value.trim();
    });
    return values;
  }

  function showErrors(form, errors) {
    Array.prototype.forEach.call(form.querySelectorAll('.field'), function (wrapper) {
      var name = wrapper.getAttribute('data-field');
      var message = errors[name] || '';
      wrapper.classList.toggle('has-error', !!message);
      wrapper.querySelector('[data-error-for="' + name + '"]').textContent = message;
    });

    var firstInvalid = Object.keys(errors)[0];
    if (firstInvalid) {
      var control = form.elements[firstInvalid];
      if (control) control.focus();
    }
  }

  function validate(values) {
    var errors = {};

    REQUIRED_FIELDS.forEach(function (fieldDef) {
      if (!values[fieldDef.name]) errors[fieldDef.name] = fieldDef.label + ' is required.';
    });

    if (values.email && !EMAIL_PATTERN.test(values.email)) {
      errors.email = 'Enter a valid email address.';
    }

    return errors;
  }

  function renderForm(id) {
    var load = id ? store.getEmployee(id) : Promise.resolve(null);

    load.then(function (employee) {
      if (id && !employee) {
        root.innerHTML = ui.notFoundView();
        return;
      }

      root.innerHTML = ui.formView(employee);
      var form = document.getElementById('employee-form');

      form.addEventListener('submit', function (event) {
        event.preventDefault();

        var values = readForm(form);
        var errors = validate(values);
        showErrors(form, errors);
        if (Object.keys(errors).length) return;

        var save = employee
          ? store.updateEmployee(employee.id, values)
          : store.createEmployee(values);

        save.then(function () {
          navigate('#/employees');
        });
      });

      var deleteButton = form.querySelector('[data-action="delete"]');
      if (deleteButton) {
        deleteButton.addEventListener('click', function () {
          if (!window.confirm('Remove ' + App.fullName(employee) + ' from the system?')) return;
          store.deleteEmployee(employee.id).then(function () {
            navigate('#/employees');
          });
        });
      }
    });
  }

  /* -------------------------------------------------------- checklist view */

  function renderChecklist(id) {
    store.getEmployee(id).then(function (employee) {
      if (!employee) {
        root.innerHTML = ui.notFoundView();
        return;
      }

      root.innerHTML = ui.checklistView(employee);

      document.getElementById('checklist').addEventListener('change', function (event) {
        var checkbox = event.target;
        if (!checkbox.matches('input[type="checkbox"]')) return;

        // Write through the store, then re-render from what the store returns -
        // the checkbox itself is never the source of truth.
        store
          .setChecklistItem(employee.id, checkbox.getAttribute('data-item-id'), checkbox.checked)
          .then(function () {
            renderChecklist(employee.id);
          });
      });
    });
  }

  /* ------------------------------------------------------------- bootstrap */

  window.addEventListener('hashchange', render);

  if (!window.location.hash) {
    window.location.hash = '#/employees';
  } else {
    render();
  }
})(window.App);
