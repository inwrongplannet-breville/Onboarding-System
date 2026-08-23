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
  var errorSlot = document.getElementById('app-error');
  var store = App.store;
  var ui = App.ui;

  // List filters survive navigation, so returning from a checklist keeps the view.
  var filters = { search: '', department: '', status: '' };
  var loadedEmployees = [];

  /* ---------------------------------------------------------- request errors */

  /*
   * Two handlers, and which one to use depends on who owns #app at the time.
   *
   *   failLoad()  for loads. The view never rendered, so as well as reporting the
   *               error it has to replace the "Loading..." placeholder with
   *               something final - otherwise the page sits there implying it is
   *               still trying.
   *   showError() for actions: save, delete, ticking a checkbox. The view is
   *               already on screen and stays usable; only the banner changes.
   */

  function showError(error) {
    errorSlot.innerHTML = ui.errorBanner(error.message);
    // The stack, the status and error.cause are worth having, but in devtools -
    // not in a banner aimed at an HR user.
    if (window.console) console.error(error);
  }

  function clearError() {
    errorSlot.innerHTML = '';
  }

  function failLoad(context) {
    return function (error) {
      showError(error);
      root.innerHTML = ui.messageView(context);
    };
  }

  function showLoading() {
    root.innerHTML = ui.loadingView();
  }

  errorSlot.addEventListener('click', function (event) {
    if (event.target.closest('[data-action="dismiss-error"]')) clearError();
  });

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

    // A banner belongs to the request that raised it, not to the next screen.
    clearError();

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
    showLoading();

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

        clearError();
        button.disabled = true;

        store.deleteEmployee(id).then(function () {
          renderList();
        }, function (error) {
          // The list is still on screen and still correct apart from this row,
          // so leave it be and just say what happened.
          button.disabled = false;
          showError(error);
        });
      });
    }, failLoad('Could not load employees.'));
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

  /**
   * The API's 400 body carries a `fields` map keyed by the same names as
   * validate() returns, phrased the same way - common/models.py was written to
   * mirror this file. So server-side validation reuses the painter we already
   * have, and a field problem lands under its input rather than in the banner.
   */
  function showSaveError(form, error) {
    if (error.status === 400 && error.fields) {
      showErrors(form, error.fields);
      return;
    }
    showError(error);
  }

  function renderForm(id) {
    // The "new employee" form has nothing to fetch, so it must not flash a
    // placeholder on its way to rendering instantly.
    if (id) showLoading();
    var load = id ? store.getEmployee(id) : Promise.resolve(null);

    load.then(function (employee) {
      if (id && !employee) {
        root.innerHTML = ui.notFoundView();
        return;
      }

      root.innerHTML = ui.formView(employee);
      var form = document.getElementById('employee-form');
      var submitting = false;

      form.addEventListener('submit', function (event) {
        event.preventDefault();

        // Enter inside a text field can fire submit before the button repaints
        // as disabled, so the flag - not the attribute - is what stops a double POST.
        if (submitting) return;

        var values = readForm(form);
        var errors = validate(values);
        showErrors(form, errors);
        if (Object.keys(errors).length) return;

        var submitButton = form.querySelector('[type="submit"]');
        var submitLabel = submitButton.textContent;

        submitting = true;
        submitButton.disabled = true;
        submitButton.textContent = 'Saving…';
        clearError();

        var save = employee
          ? store.updateEmployee(employee.id, values)
          : store.createEmployee(values);

        save.then(function () {
          navigate('#/employees');   // nothing to restore; navigating discards this DOM
        }, function (error) {
          submitting = false;
          submitButton.disabled = false;
          submitButton.textContent = submitLabel;
          showSaveError(form, error);
        });
      });

      var deleteButton = form.querySelector('[data-action="delete"]');
      if (deleteButton) {
        deleteButton.addEventListener('click', function () {
          if (!window.confirm('Remove ' + App.fullName(employee) + ' from the system?')) return;

          clearError();
          deleteButton.disabled = true;

          store.deleteEmployee(employee.id).then(function () {
            navigate('#/employees');
          }, function (error) {
            // The form is still filled in and still valid; keep it usable.
            deleteButton.disabled = false;
            showError(error);
          });
        });
      }
    }, failLoad('Could not load this employee.'));
  }

  /* -------------------------------------------------------- checklist view */

  function renderChecklist(id) {
    showLoading();

    store.getEmployee(id).then(function (employee) {
      if (!employee) {
        root.innerHTML = ui.notFoundView();
        return;
      }
      paintChecklist(employee);
    }, failLoad('Could not load this checklist.'));
  }

  /*
   * Split out from renderChecklist so a tick can repaint from the PATCH response
   * instead of triggering a second GET - and so the repaint costs no placeholder
   * flash on every click.
   */
  function paintChecklist(employee) {
    root.innerHTML = ui.checklistView(employee);

    document.getElementById('checklist').addEventListener('change', function (event) {
      var checkbox = event.target;
      if (!checkbox.matches('input[type="checkbox"]')) return;

      var itemId = checkbox.getAttribute('data-item-id');
      var intended = checkbox.checked;

      clearError();
      checkbox.disabled = true;   // one control, not the whole page - it is a short round trip

      store.setChecklistItem(employee.id, itemId, intended).then(function (updated) {
        // The checkbox is never the source of truth: repaint from the employee the
        // server just returned, progress bar and status badge included.
        paintChecklist(updated);
      }, function (error) {
        // The write did not land, so put the box back to what the server still
        // believes. We deliberately don't re-fetch to confirm that - the request
        // that would tell us is the one that just failed.
        checkbox.checked = !intended;
        checkbox.disabled = false;
        showError(error);
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
