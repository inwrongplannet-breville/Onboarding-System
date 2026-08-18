/**
 * View rendering. Every function here is pure: state in, HTML string out.
 * Nothing in this file touches the store or the DOM - app.js does both.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function statusBadge(employee) {
    var status = App.computeStatus(employee);
    var modifier = status.toLowerCase().replace(/\s+/g, '-');
    return '<span class="badge badge-' + modifier + '">' + escapeHtml(status) + '</span>';
  }

  function progressCell(employee) {
    var p = App.progress(employee);
    return '' +
      '<div class="progress">' +
        '<div class="progress-label">' + p.done + ' of ' + p.total + '</div>' +
        '<div class="progress-track">' +
          '<div class="progress-bar" style="width:' + p.percent + '%"></div>' +
        '</div>' +
      '</div>';
  }

  function options(values, selected) {
    return values.map(function (value) {
      var isSelected = value === selected ? ' selected' : '';
      return '<option value="' + escapeHtml(value) + '"' + isSelected + '>' +
        escapeHtml(value) + '</option>';
    }).join('');
  }

  function field(config) {
    var required = config.required ? ' <span class="req">*</span>' : '';
    var control;

    if (config.type === 'select') {
      control = '<select id="' + config.name + '" name="' + config.name + '">' +
        '<option value="">Select&hellip;</option>' +
        options(config.choices, config.value) +
        '</select>';
    } else {
      control = '<input id="' + config.name + '" name="' + config.name + '"' +
        ' type="' + (config.type || 'text') + '"' +
        ' value="' + escapeHtml(config.value || '') + '"' +
        (config.placeholder ? ' placeholder="' + escapeHtml(config.placeholder) + '"' : '') +
        '>';
    }

    return '' +
      '<div class="field' + (config.full ? ' full' : '') + '" data-field="' + config.name + '">' +
        '<label for="' + config.name + '">' + escapeHtml(config.label) + required + '</label>' +
        control +
        '<p class="error-text" data-error-for="' + config.name + '"></p>' +
      '</div>';
  }

  App.ui = {
    escapeHtml: escapeHtml,

    /** Table rows only - re-rendered on its own when filters change. */
    employeeRows: function (employees) {
      if (!employees.length) {
        return '<tr><td class="empty-state" colspan="6">' +
          'No employees match this view.</td></tr>';
      }

      return employees.map(function (employee) {
        return '' +
          '<tr data-id="' + escapeHtml(employee.id) + '">' +
            '<td class="name-cell">' + escapeHtml(App.fullName(employee)) +
              '<small>' + escapeHtml(employee.email) + '</small></td>' +
            '<td>' + escapeHtml(employee.department) + '</td>' +
            '<td>' + escapeHtml(employee.jobTitle) + '</td>' +
            '<td>' + escapeHtml(App.formatDate(employee.startDate)) + '</td>' +
            '<td>' + progressCell(employee) + ' ' + statusBadge(employee) + '</td>' +
            '<td class="actions">' +
              '<a class="btn-link" href="#/employees/' + encodeURIComponent(employee.id) + '/checklist">Checklist</a>' +
              '<a class="btn-link" href="#/employees/' + encodeURIComponent(employee.id) + '/edit">Edit</a>' +
              '<button class="btn-link danger" type="button" data-action="delete">Delete</button>' +
            '</td>' +
          '</tr>';
      }).join('');
    },

    /** Count line under the heading - kept in sync with the filtered rows. */
    countLabel: function (shown, total) {
      if (shown === total) return total + ' record' + (total === 1 ? '' : 's');
      return 'Showing ' + shown + ' of ' + total + ' records';
    },

    listView: function (employees, filters) {
      return '' +
        '<div class="page-head">' +
          '<div>' +
            '<h1>Employees</h1>' +
            '<p class="subtitle" id="record-count"></p>' +
          '</div>' +
          '<a class="btn btn-primary" href="#/employees/new">Add Employee</a>' +
        '</div>' +

        '<div class="filters">' +
          '<input id="search" type="search" placeholder="Search by name or email"' +
            ' value="' + escapeHtml(filters.search || '') + '">' +
          '<select id="department-filter">' +
            '<option value="">All departments</option>' +
            options(App.DEPARTMENTS, filters.department) +
          '</select>' +
          '<select id="status-filter">' +
            '<option value="">All statuses</option>' +
            options(['Pending', 'In Progress', 'Onboarded'], filters.status) +
          '</select>' +
        '</div>' +

        '<table>' +
          '<thead><tr>' +
            '<th>Name</th><th>Department</th><th>Job title</th>' +
            '<th>Start date</th><th>Onboarding</th><th></th>' +
          '</tr></thead>' +
          '<tbody id="employee-rows"></tbody>' +
        '</table>';
    },

    formView: function (employee) {
      var isEdit = !!employee;
      var data = employee || {};

      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<h1>' + (isEdit ? 'Edit Employee' : 'Add Employee') + '</h1>' +
        '<p class="subtitle">' + (isEdit
          ? 'Updating ' + escapeHtml(App.fullName(data)) +
            '. The onboarding checklist is managed separately.'
          : 'The new hire starts with a fresh onboarding checklist.') + '</p>' +

        '<form class="form" id="employee-form" novalidate>' +
          '<div class="form-grid">' +
            field({ name: 'firstName', label: 'First name', value: data.firstName, required: true }) +
            field({ name: 'lastName', label: 'Last name', value: data.lastName, required: true }) +
            field({ name: 'email', label: 'Work email', type: 'email', value: data.email, required: true, placeholder: 'name@breville.com' }) +
            field({ name: 'phone', label: 'Phone', value: data.phone, placeholder: '+61 4XX XXX XXX' }) +
            field({ name: 'department', label: 'Department', type: 'select', choices: App.DEPARTMENTS, value: data.department, required: true }) +
            field({ name: 'jobTitle', label: 'Job title', value: data.jobTitle, required: true }) +
            field({ name: 'manager', label: 'Reporting manager', value: data.manager }) +
            field({ name: 'startDate', label: 'Start date', type: 'date', value: data.startDate, required: true }) +
            field({ name: 'employmentType', label: 'Employment type', type: 'select', choices: App.EMPLOYMENT_TYPES, value: data.employmentType || 'Full-time', required: true }) +
          '</div>' +

          '<div class="btn-row">' +
            '<button class="btn btn-primary" type="submit">' +
              (isEdit ? 'Save Changes' : 'Add Employee') + '</button>' +
            '<a class="btn" href="#/employees">Cancel</a>' +
            (isEdit
              ? '<button class="btn-link danger" type="button" data-action="delete" style="margin-left:auto">Delete employee</button>'
              : '') +
          '</div>' +
        '</form>';
    },

    checklistView: function (employee) {
      var p = App.progress(employee);

      var items = employee.checklist.map(function (item) {
        return '' +
          '<li class="' + (item.done ? 'done' : '') + '">' +
            '<label>' +
              '<input type="checkbox" data-item-id="' + escapeHtml(item.id) + '"' +
                (item.done ? ' checked' : '') + '>' +
              '<span class="item-label">' + escapeHtml(item.label) + '</span>' +
              '<span class="owner">' + escapeHtml(item.owner) + '</span>' +
            '</label>' +
          '</li>';
      }).join('');

      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<div class="page-head">' +
          '<div>' +
            '<h1>' + escapeHtml(App.fullName(employee)) + '</h1>' +
            '<p class="subtitle">' + escapeHtml(employee.jobTitle) + ' &middot; ' +
              escapeHtml(employee.department) + '</p>' +
          '</div>' +
          '<a class="btn" href="#/employees/' + encodeURIComponent(employee.id) + '/edit">Edit details</a>' +
        '</div>' +

        '<div class="summary-card">' +
          statusBadge(employee) +
          ' <strong id="progress-text">' + p.done + ' of ' + p.total + ' complete</strong>' +
          '<div class="progress-track" style="margin-top:8px">' +
            '<div class="progress-bar" id="progress-bar" style="width:' + p.percent + '%"></div>' +
          '</div>' +
          '<dl class="summary-grid">' +
            '<div><dt>Start date</dt><dd>' + escapeHtml(App.formatDate(employee.startDate)) + '</dd></div>' +
            '<div><dt>Manager</dt><dd>' + escapeHtml(employee.manager || '-') + '</dd></div>' +
            '<div><dt>Employment type</dt><dd>' + escapeHtml(employee.employmentType || '-') + '</dd></div>' +
            '<div><dt>Email</dt><dd>' + escapeHtml(employee.email) + '</dd></div>' +
            '<div><dt>Phone</dt><dd>' + escapeHtml(employee.phone || '-') + '</dd></div>' +
          '</dl>' +
        '</div>' +

        '<h2>Onboarding checklist</h2>' +
        '<ul class="checklist" id="checklist">' + items + '</ul>' +

        '<h2>Documents</h2>' +
        '<div class="stub">Offer letter, ID proof and signed policy uploads land here ' +
          'once document storage (S3) is built in a later phase.</div>';
    },

    notFoundView: function () {
      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<h1>Not found</h1>' +
        '<p class="subtitle">That employee record does not exist.</p>';
    }
  };
})(window.App);
