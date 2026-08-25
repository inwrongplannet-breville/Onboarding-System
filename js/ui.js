/**
 * View rendering. Every function here is pure: state in, HTML string out.
 * Nothing in this file touches the store or the DOM - app.js does both.
 *
 * There is no employee schema in this file and none anywhere else in js/. The
 * two dropdown enums used to live in js/model.js alongside client-side copies of
 * the status and progress rules; all four were a second implementation of what
 * common/models.py already owns, and the pair drifted once already - a rounding
 * mismatch that had the API and the UI reporting different percentages for the
 * same checklist.
 *
 * So: `status` and `progress` are rendered exactly as the API sends them, and
 * the dropdown values are collected from the records the API returned. What is
 * left here is presentation - escaping, formatting a date, joining a first and
 * last name - which has no server-side counterpart to disagree with.
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

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // Mirrors COMMENT_MAX_LENGTH in common/models.py. Duplicated deliberately and
  // narrowly: maxlength on the textarea stops someone pasting an email thread
  // and only finding out after a round trip. The server still enforces it - this
  // is a courtesy, not the rule.
  var COMMENT_MAX_LENGTH = 500;

  function fullName(employee) {
    return (employee.firstName + ' ' + employee.lastName).trim();
  }

  /** "2026-09-01" -> "1 Sep 2026". Parsed by hand to dodge timezone shifts. */
  function formatDate(isoDate) {
    if (!isoDate) return '-';
    var parts = String(isoDate).split('-');
    if (parts.length !== 3) return isoDate;
    var month = MONTHS[Number(parts[1]) - 1];
    if (!month) return isoDate;
    return Number(parts[2]) + ' ' + month + ' ' + parts[0];
  }

  // Both of these read a value the server derived. Nothing is invented when it
  // is absent - an employee with no status renders no badge, which is visibly
  // wrong, where a defaulted "Pending" would be invisibly wrong.
  function statusBadge(employee) {
    if (!employee.status) return '';
    var modifier = employee.status.toLowerCase().replace(/\s+/g, '-');
    return '<span class="badge badge-' + modifier + '">' +
      escapeHtml(employee.status) + '</span>';
  }

  function progressCell(employee) {
    var p = employee.progress;
    if (!p) return '';

    // A bare div's width is invisible to a screen reader, so the same fraction
    // the bar draws is stated on the element as well.
    var labelId = 'progress-label-' + employee.id;

    return '' +
      '<div class="progress">' +
        '<div class="progress-label" id="' + escapeHtml(labelId) + '">' +
          p.done + ' of ' + p.total + '</div>' +
        '<div class="progress-track" role="progressbar"' +
          ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + p.percent + '"' +
          ' aria-labelledby="' + escapeHtml(labelId) + '">' +
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

  /*
   * Inline SVG rather than an icon font or an image: no extra request, it
   * inherits currentColor, and it scales with the text. Filled when there is
   * something to read, outlined when there is not - so the state is visible
   * down the whole list without opening anything.
   */
  function commentIcon(hasComment) {
    // A rounded rectangle with a tail, drawn on the 16px grid so both variants
    // land on whole pixels. Circles read as blobs at this size.
    var bubble = 'M3 2.75h10A1.75 1.75 0 0 1 14.75 4.5v4.75A1.75 1.75 0 0 1 13 11h-6.4' +
      'L4 13.4V11H3A1.75 1.75 0 0 1 1.25 9.25V4.5A1.75 1.75 0 0 1 3 2.75Z';

    return '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"' +
      ' focusable="false">' +
      '<path d="' + bubble + '"' +
        (hasComment
          ? ' fill="currentColor"'
          : ' fill="none" stroke="currentColor" stroke-width="1.3"' +
            ' stroke-linejoin="round"') +
      '/></svg>';
  }

  /*
   * The archived banner. Its wording leans on employee.archivedAs rather than
   * looking at the checklist, for the reason in this file's header: the server
   * decides what state a record archived into, and a second opinion here is how
   * the two start disagreeing.
   */
  function archivedNotice(employee) {
    if (!employee.archived) return '';

    var when = employee.archivedAt
      ? ' on ' + escapeHtml(formatDate(employee.archivedAt.slice(0, 10)))
      : '';

    return '' +
      '<div class="archived-notice" role="note">' +
        '<strong>Archived' + when + '</strong> &middot; ' +
        escapeHtml(employee.archivedAs) + '. ' +
        'This record is kept for reference and can no longer be changed.' +
      '</div>';
  }

  function commentButton(item, isEditing, frozen) {
    // One control, two jobs, so it has to name the one it is about to do.
    var verb = isEditing
      ? 'Close the comment box on '
      : (item.comment ? 'Edit the comment on ' : 'Add a comment to ');

    return '<button type="button" class="comment-btn' +
      (item.comment ? ' has-comment' : '') + '"' +
      ' data-action="comment" data-item-id="' + escapeHtml(item.id) + '"' +
      (frozen ? ' disabled' : '') +
      ' aria-expanded="' + (isEditing ? 'true' : 'false') + '"' +
      // title for the mouse, aria-label for everyone else. Same words, because
      // an icon with no text needs to answer "what is this" both ways.
      ' title="' + escapeHtml(verb + item.label) + '"' +
      ' aria-label="' + escapeHtml(verb + item.label) + '">' +
      commentIcon(!!item.comment) +
      '</button>';
  }

  function commentEditor(item, draft) {
    var inputId = 'comment-input';

    return '' +
      '<div class="comment-editor">' +
        '<label class="sr-only" for="' + inputId + '">Comment on ' +
          escapeHtml(item.label) + '</label>' +
        '<textarea id="' + inputId + '" data-item-id="' + escapeHtml(item.id) + '"' +
          ' rows="3" maxlength="' + COMMENT_MAX_LENGTH + '"' +
          ' aria-describedby="comment-error comment-hint"' +
          ' placeholder="Anything the next person needs to know">' +
          escapeHtml(draft) +
        '</textarea>' +
        '<p class="error-text" id="comment-error" data-error-for="comment"></p>' +
        '<div class="comment-actions">' +
          '<button class="btn btn-primary" type="button" data-action="save-comment">' +
            'Save comment</button>' +
          '<button class="btn" type="button" data-action="cancel-comment">Cancel</button>' +
          (item.comment
            ? '<button class="btn-link danger" type="button"' +
              ' data-action="delete-comment">Remove</button>'
            : '') +
          '<span class="comment-hint" id="comment-hint">Ctrl+Enter saves, Esc cancels</span>' +
        '</div>' +
      '</div>';
  }

  function field(config) {
    // The asterisk is decoration - aria-required is what actually says
    // "required", and reading "star" out loud says nothing.
    var required = config.required
      ? ' <span class="req" aria-hidden="true">*</span>'
      : '';

    // aria-describedby is wired up whether or not there is an error yet. An
    // empty description is ignored; attaching it only once a message appears is
    // how that message ends up never announced.
    var errorId = 'error-' + config.name;
    var hintId = 'hint-' + config.name;
    var shared = ' aria-describedby="' + errorId +
      (config.hint ? ' ' + hintId : '') + '" aria-invalid="false"' +
      (config.required ? ' aria-required="true"' : '');
    var control;

    if (config.type === 'select' && !(config.choices || []).length) {
      // The choices are collected from existing employees, so on an empty table
      // there are none - and a select with no options is a form nobody can
      // submit. A text box keeps the very first hire creatable; the server
      // validates the value against the real enum either way and names it in the
      // 400 if it is wrong.
      control = '<input id="' + config.name + '" name="' + config.name + '"' +
        ' type="text" value="' + escapeHtml(config.value || '') + '"' + shared +
        ' placeholder="No existing values to pick from - type one">';
    } else if (config.type === 'select') {
      control = '<select id="' + config.name + '" name="' + config.name + '"' +
        shared + '>' +
        '<option value="">Select&hellip;</option>' +
        options(config.choices, config.value) +
        '</select>';
    } else {
      control = '<input id="' + config.name + '" name="' + config.name + '"' +
        ' type="' + (config.type || 'text') + '"' +
        ' value="' + escapeHtml(config.value || '') + '"' + shared +
        // readonly, never disabled. A disabled input is skipped by keyboard
        // navigation and is not read out, so the one field that explains why it
        // cannot be edited would be the one field nobody hears about.
        (config.readonly ? ' readonly' : '') +
        (config.placeholder ? ' placeholder="' + escapeHtml(config.placeholder) + '"' : '') +
        '>';
    }

    // Sits between the control and the error line, and is wired into
    // aria-describedby above so it is read with the field rather than passed
    // over. Static guidance - the error text is a separate element that starts
    // empty.
    var hint = config.hint
      ? '<p class="field-hint" id="' + hintId + '">' + escapeHtml(config.hint) + '</p>'
      : '';

    return '' +
      '<div class="field' + (config.full ? ' full' : '') +
        (config.readonly ? ' is-readonly' : '') +
        '" data-field="' + config.name + '">' +
        '<label for="' + config.name + '">' + escapeHtml(config.label) + required + '</label>' +
        control +
        hint +
        '<p class="error-text" id="' + errorId +
          '" data-error-for="' + config.name + '"></p>' +
      '</div>';
  }

  App.ui = {
    escapeHtml: escapeHtml,
    fullName: fullName,
    formatDate: formatDate,

    /** Table rows only - re-rendered on its own when filters change. */
    employeeRows: function (employees) {
      if (!employees.length) {
        return '<tr><td class="empty-state" colspan="7">' +
          'No employees match this view.</td></tr>';
      }

      return employees.map(function (employee) {
        // "Edit" six times over tells a screen-reader user nothing about which
        // row they are on, so every control in the row carries the name.
        var name = escapeHtml(fullName(employee));
        var href = '#/employees/' + encodeURIComponent(employee.id);

        return '' +
          '<tr data-id="' + escapeHtml(employee.id) + '">' +
            '<td class="id-cell">' + escapeHtml(employee.id) + '</td>' +
            '<td class="name-cell">' + escapeHtml(fullName(employee)) +
              '<small>' + escapeHtml(employee.email) + '</small></td>' +
            '<td>' + escapeHtml(employee.department) + '</td>' +
            '<td>' + escapeHtml(employee.jobTitle) + '</td>' +
            '<td>' + escapeHtml(formatDate(employee.startDate)) + '</td>' +
            '<td>' + progressCell(employee) + ' ' + statusBadge(employee) + '</td>' +
            '<td class="actions">' +
              '<a class="btn-link" href="' + href + '/checklist"' +
                ' aria-label="Onboarding checklist for ' + name + '">Checklist</a>' +
              '<a class="btn-link" href="' + href + '/edit"' +
                ' aria-label="Edit ' + name + '">Edit</a>' +
              '<button class="btn-link danger" type="button" data-action="delete"' +
                ' aria-label="Delete ' + name + '">Delete</button>' +
            '</td>' +
          '</tr>';
      }).join('');
    },

    /** Count line under the heading - kept in sync with the filtered rows. */
    countLabel: function (shown, total) {
      if (shown === total) return total + ' record' + (total === 1 ? '' : 's');
      return 'Showing ' + shown + ' of ' + total + ' records';
    },

    /**
     * `facets` is what the loaded records happen to contain - see collectFacets
     * in app.js. Filtering by a department nobody is in would return nothing, so
     * offering it would be a lie.
     */
    listView: function (employees, filters, facets) {
      return '' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">Employees</h1>' +
            // Polite, so re-filtering as you type announces the new count
            // without cutting off whatever is being read.
            '<p class="subtitle" id="record-count" aria-live="polite"></p>' +
          '</div>' +
          '<a class="btn btn-primary" href="#/employees/new">Add Employee</a>' +
        '</div>' +

        '<div class="filters">' +
          '<input id="search" type="search" placeholder="Search by ID, name or email"' +
            ' aria-label="Search employees by employee ID, name or email"' +
            ' value="' + escapeHtml(filters.search || '') + '">' +
          '<select id="department-filter" aria-label="Filter by department">' +
            '<option value="">All departments</option>' +
            options(facets.departments, filters.department) +
          '</select>' +
          '<select id="status-filter" aria-label="Filter by onboarding status">' +
            '<option value="">All statuses</option>' +
            options(facets.statuses, filters.status) +
          '</select>' +
        '</div>' +

        '<table>' +
          '<thead><tr>' +
            '<th scope="col">Employee ID</th>' +
            '<th scope="col">Name</th><th scope="col">Department</th>' +
            '<th scope="col">Job title</th><th scope="col">Start date</th>' +
            '<th scope="col">Onboarding</th>' +
            // Not left empty: a column with no header is a column a screen
            // reader cannot name when it reads the cells under it.
            '<th scope="col"><span class="sr-only">Actions</span></th>' +
          '</tr></thead>' +
          '<tbody id="employee-rows"></tbody>' +
        '</table>';
    },

    formView: function (employee, facets) {
      var isEdit = !!employee;
      var data = employee || {};

      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<h1 tabindex="-1">' + (isEdit ? 'Edit Employee' : 'Add Employee') + '</h1>' +
        '<p class="subtitle">' + (isEdit
          ? 'Updating ' + escapeHtml(fullName(data)) +
            '. The onboarding checklist is managed separately.'
          : 'The new hire starts with a fresh onboarding checklist.') + '</p>' +

        '<form class="form" id="employee-form" novalidate>' +
          '<div class="form-grid">' +
            // First, because it is the record's identity and the thing that has
            // to be right before anything else matters. Read-only when editing:
            // it is the DynamoDB partition key, and the API has no way to change
            // it - offering an editable box would promise a rename that cannot
            // happen. Still submitted (readonly, not disabled), but
            // store.updateEmployee drops it.
            field({
              name: 'employeeId',
              label: 'Employee ID',
              value: isEdit ? data.id : '',
              required: true,
              full: true,
              readonly: isEdit,
              placeholder: 'e.g. E1024',
              hint: isEdit
                ? 'An employee ID cannot be changed once the record exists.'
                : 'Letters, digits and hyphens. This becomes the permanent id for this record.'
            }) +
            field({ name: 'firstName', label: 'First name', value: data.firstName, required: true }) +
            field({ name: 'lastName', label: 'Last name', value: data.lastName, required: true }) +
            field({ name: 'email', label: 'Work email', type: 'email', value: data.email, required: true, placeholder: 'name@breville.com' }) +
            field({ name: 'phone', label: 'Phone', value: data.phone, placeholder: '+61 4XX XXX XXX' }) +
            field({ name: 'department', label: 'Department', type: 'select', choices: facets.departments, value: data.department, required: true }) +
            field({ name: 'jobTitle', label: 'Job title', value: data.jobTitle, required: true }) +
            field({ name: 'manager', label: 'Reporting manager', value: data.manager }) +
            field({ name: 'startDate', label: 'Start date', type: 'date', value: data.startDate, required: true }) +
            field({ name: 'employmentType', label: 'Employment type', type: 'select', choices: facets.employmentTypes, value: data.employmentType, required: true }) +
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

    /**
      * `editing` is `{ itemId, draft }` for the one comment box that is open, or
      * null. Held by app.js rather than in the DOM, because a tick anywhere on
      * the page repaints this whole view from the server's response - and a
      * half-written comment must survive that.
      */
    checklistView: function (employee, editing) {
      var p = employee.progress;
      // Archived records are frozen server side. Presenting live checkboxes over
      // one would offer the user an action that can only ever fail with a 409.
      var frozen = !!employee.archived;

      var items = employee.checklist.map(function (item) {
        var isEditing = !!editing && editing.itemId === item.id;

        var trailing = '';
        if (isEditing) {
          trailing = commentEditor(item, editing.draft);
        } else if (item.comment) {
          trailing = '<p class="item-comment">' + escapeHtml(item.comment) + '</p>';
        }

        return '' +
          '<li class="' + (item.done ? 'done' : '') + '">' +
            // The button sits OUTSIDE the label. Inside it, every click on the
            // comment icon would also toggle the checkbox.
            '<div class="item-row">' +
              '<label>' +
                '<input type="checkbox" data-item-id="' + escapeHtml(item.id) + '"' +
                  (item.done ? ' checked' : '') + (frozen ? ' disabled' : '') + '>' +
                '<span class="item-label">' + escapeHtml(item.label) + '</span>' +
                '<span class="owner">' + escapeHtml(item.owner) + '</span>' +
              '</label>' +
              commentButton(item, isEditing, frozen) +
            '</div>' +
            trailing +
          '</li>';
      }).join('');

      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">' + escapeHtml(fullName(employee)) + '</h1>' +
            '<p class="subtitle">' + escapeHtml(employee.jobTitle) + ' &middot; ' +
              escapeHtml(employee.department) + '</p>' +
          '</div>' +
          (frozen
            ? ''
            : '<a class="btn" href="#/employees/' + encodeURIComponent(employee.id) +
              '/edit">Edit details</a>') +
        '</div>' +

        archivedNotice(employee) +

        '<div class="summary-card">' +
          statusBadge(employee) +
          ' <strong id="progress-text">' + p.done + ' of ' + p.total + ' complete</strong>' +
          '<div class="progress-track" style="margin-top:8px" role="progressbar"' +
            ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + p.percent + '"' +
            ' aria-labelledby="progress-text">' +
            '<div class="progress-bar" id="progress-bar" style="width:' + p.percent + '%"></div>' +
          '</div>' +
          '<dl class="summary-grid">' +
            '<div><dt>Employee ID</dt><dd>' + escapeHtml(employee.id) + '</dd></div>' +
            '<div><dt>Start date</dt><dd>' + escapeHtml(formatDate(employee.startDate)) + '</dd></div>' +
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

    /*
     * Stands in for the edit form on an archived employee. Not notFoundView:
     * the record is right there and readable, it just cannot be edited, and
     * "does not exist" would send someone looking for a record that does.
     */
    archivedView: function (employee) {
      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<h1 tabindex="-1">' + escapeHtml(fullName(employee)) + '</h1>' +
        '<p class="subtitle">' + escapeHtml(employee.jobTitle) + ' &middot; ' +
          escapeHtml(employee.department) + '</p>' +
        archivedNotice(employee) +
        '<p><a class="btn" href="#/employees/' + encodeURIComponent(employee.id) +
          '/checklist">View onboarding record</a></p>';
    },

    notFoundView: function () {
      return '' +
        '<a class="back-link" href="#/employees">&larr; Back to employees</a>' +
        '<h1 tabindex="-1">Not found</h1>' +
        '<p class="subtitle">That employee record does not exist.</p>';
    },

    /* --------------------------------------------------- request feedback */

    loadingView: function () {
      // role=status so the wait itself is announced; app.js pairs this with
      // aria-busy on #app.
      return '<p class="empty-state" role="status">Loading&hellip;</p>';
    },

    /** Replaces a view that could not be loaded, so "Loading..." is never the last word. */
    messageView: function (text) {
      return '<p class="empty-state">' + escapeHtml(text) + '</p>';
    },

    errorBanner: function (message) {
      return '' +
        '<div class="alert">' +
          '<span>' + escapeHtml(message) + '</span>' +
          '<button type="button" class="btn-link" data-action="dismiss-error">Dismiss</button>' +
        '</div>';
    }
  };
})(window.App);
