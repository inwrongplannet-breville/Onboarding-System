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

  /*
   * A file size a person can read. Binary units, because that is what a file
   * manager shows and a 240 KB resume matching the OS is worth more than being
   * pedantic about KiB.
   */
  function formatBytes(bytes) {
    if (typeof bytes !== 'number' || bytes < 0) return '';
    if (bytes < 1024) return bytes + ' B';
    var kb = bytes / 1024;
    if (kb < 1024) return Math.round(kb) + ' KB';
    return (Math.round(kb / 1024 * 10) / 10) + ' MB';
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

  /*
   * The bar plus the fraction it is drawing, for a list card.
   *
   * `idSuffix` keeps the label's id unique: the same employee can be rendered
   * twice on one page in principle, and a duplicated id would point every
   * aria-labelledby at whichever copy came first.
   */
  function progressBlock(employee, idSuffix) {
    var p = employee.progress;
    if (!p) return '';

    // A bare div's width is invisible to a screen reader, so the same fraction
    // the bar draws is stated on the element as well.
    var labelId = 'progress-label-' + (idSuffix || 'card') + '-' + employee.id;

    return '' +
      '<div class="progress">' +
        '<div class="progress-track" role="progressbar"' +
          ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + p.percent + '"' +
          ' aria-labelledby="' + escapeHtml(labelId) + '">' +
          '<div class="progress-bar" style="width:' + p.percent + '%"></div>' +
        '</div>' +
        '<div class="progress-label" id="' + escapeHtml(labelId) + '">' +
          p.done + ' of ' + p.total + ' complete</div>' +
      '</div>';
  }

  /**
   * "3 interns" / "1 employee" - the simple case of countLabel below, for the
   * two staff dashboards, which show every record with no filtering to report
   * a "showing N of M" state for.
   */
  function recordCountLabel(count, noun) {
    return count + ' ' + noun + (count === 1 ? '' : 's');
  }

  /** A <select> of employees for a reporting-manager picker, value = employee id. */
  function managerOptionsHtml(employees, selectedId) {
    return employees.map(function (employee) {
      var isSelected = employee.id === selectedId ? ' selected' : '';
      return '<option value="' + escapeHtml(employee.id) + '"' + isSelected + '>' +
        escapeHtml(fullName(employee)) + ' (' + escapeHtml(employee.id) + ')</option>';
    }).join('');
  }

  /**
   * Days left in the seven-day undo window, or 0 once it has closed.
   * Mirrors common.models.unpromote_window_open/UNPROMOTE_WINDOW_DAYS - see
   * that module for why the deadline lives in exactly one place server side;
   * this is only ever used to decide whether to show the "Undo move" button,
   * never to enforce anything.
   */
  var UNPROMOTE_WINDOW_DAYS = 7;

  function undoWindowDaysLeft(onboardedAt) {
    if (!onboardedAt) return 0;
    var elapsedMs = Date.now() - new Date(onboardedAt).getTime();
    var daysLeft = UNPROMOTE_WINDOW_DAYS - Math.floor(elapsedMs / (24 * 60 * 60 * 1000));
    return daysLeft > 0 ? daysLeft : 0;
  }

  /** The "Undo move" control, shared by the intern and employee tracking cards. */
  function undoMoveControl(record) {
    var daysLeft = undoWindowDaysLeft(record.onboardedAt);
    if (daysLeft <= 0) {
      return '<p class="view-note">The undo window has closed.</p>';
    }
    return '' +
      '<button class="btn-link" type="button" data-action="unpromote"' +
        ' aria-label="Undo move for ' + escapeHtml(fullName(record)) + '">' +
        'Undo move' +
      '</button>' +
      '<span class="dash-card-chip">' + daysLeft + ' day' + (daysLeft === 1 ? '' : 's') +
        ' left to undo</span>';
  }

  /** One card for the Interns dashboard - mirrors the onboarding employee card. */
  function internCard(intern, managerOptionsHtml) {
    var name = escapeHtml(fullName(intern));

    return '' +
      '<li class="employee-card" data-id="' + escapeHtml(intern.id) + '">' +
        '<div class="ec-top">' +
          '<span class="ec-id">' + escapeHtml(intern.id) + '</span>' +
          statusBadge(intern) +
        '</div>' +
        '<div>' +
          '<h3 class="ec-name">' + name + '</h3>' +
          '<p class="ec-role">' + escapeHtml(intern.jobTitle) +
            ' &middot; ' + escapeHtml(intern.department) + '</p>' +
        '</div>' +
        '<p class="ec-email">' + escapeHtml(intern.email) + '</p>' +
        '<p><strong>Reports to:</strong> ' +
          escapeHtml(intern.reportingManagerId || '-') + '</p>' +
        '<p class="ec-start">' +
          '<span class="ec-start-label">Joined</span>' +
          escapeHtml(formatDate(intern.joinedOn)) +
        '</p>' +
        '<div class="ec-foot">' +
          '<label>' +
            '<span class="sr-only">Reassign ' + name + ' to</span>' +
            '<select data-role="reassign-manager" aria-label="Reassign ' + name + ' to a different manager">' +
              '<option value="">Reassign manager&hellip;</option>' +
              managerOptionsHtml +
            '</select>' +
          '</label>' +
          '<button class="btn-link" type="button" data-action="reassign"' +
            ' aria-label="Confirm reassignment for ' + name + '">Reassign</button>' +
        '</div>' +
        undoMoveControl(intern) +
      '</li>';
  }

  /** One card for the Employee Tracking dashboard. */
  function staffEmployeeCard(employee) {
    var name = escapeHtml(fullName(employee));
    var internCount = (employee.interns || []).length;

    return '' +
      '<li class="employee-card" data-id="' + escapeHtml(employee.id) + '">' +
        '<div class="ec-top">' +
          '<span class="ec-id">' + escapeHtml(employee.id) + '</span>' +
          statusBadge(employee) +
        '</div>' +
        '<div>' +
          '<h3 class="ec-name">' + name + '</h3>' +
          '<p class="ec-role">' + escapeHtml(employee.jobTitle) +
            ' &middot; ' + escapeHtml(employee.department) + '</p>' +
        '</div>' +
        '<p class="ec-email">' + escapeHtml(employee.email) + '</p>' +
        (internCount
          ? '<p><strong>Interns:</strong> ' + internCount + '</p>'
          : '') +
        '<p class="ec-start">' +
          '<span class="ec-start-label">Joined</span>' +
          escapeHtml(formatDate(employee.joinedOn)) +
        '</p>' +
        undoMoveControl(employee) +
      '</li>';
  }

  /**
   * The "Move to main employee dashboard" / "Move to intern dashboard"
   * control on the checklist screen. `managers` is:
   *   null       still loading - only relevant for an intern, so the button
   *              is shown disabled with a "Loading managers..." note
   *   [] or more the employee-table list, for the reporting-manager <select>
   *
   * Absent entirely on an archived record - archiving already means "this
   * onboarding is over", and promoting one would double up on that.
   */
  function promoteSection(employee, managers) {
    if (employee.archived) return '';

    if (employee.status !== 'Onboarded') {
      var remaining = employee.progress.total - employee.progress.done;
      return '' +
        '<div class="summary-card" id="promote-section">' +
          '<p class="view-note">Finish the checklist before moving ' +
            escapeHtml(fullName(employee)) + ' off onboarding - ' + remaining +
            ' item' + (remaining === 1 ? '' : 's') + ' left.</p>' +
        '</div>';
    }

    if (employee.employmentType !== 'Intern') {
      return '' +
        '<div class="summary-card" id="promote-section">' +
          '<button class="btn btn-primary" type="button" data-action="promote-employee">' +
            'Move to main employee dashboard' +
          '</button>' +
        '</div>';
    }

    if (managers === null) {
      return '' +
        '<div class="summary-card" id="promote-section">' +
          '<button class="btn btn-primary" type="button" disabled>' +
            'Move to intern dashboard' +
          '</button>' +
          '<p class="view-note">Loading reporting managers&hellip;</p>' +
        '</div>';
    }

    if (!managers.length) {
      return '' +
        '<div class="summary-card" id="promote-section">' +
          '<p class="view-note">No one is on the employee dashboard yet, so there is no ' +
            'reporting manager to assign. Promote a manager first.</p>' +
        '</div>';
    }

    return '' +
      '<div class="summary-card" id="promote-section">' +
        '<label>' +
          '<span>Reporting manager</span>' +
          '<select id="reporting-manager">' +
            '<option value="">Select&hellip;</option>' +
            managerOptionsHtml(managers, null) +
          '</select>' +
        '</label>' +
        '<button class="btn btn-primary" type="button" data-action="promote-intern">' +
          'Move to intern dashboard' +
        '</button>' +
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

    // 16px, not 15 - matched to checklistStateIcon's grid so the two icon
    // families line up wherever a row shows both.
    return '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"' +
      ' focusable="false">' +
      '<path d="' + bubble + '"' +
        (hasComment
          ? ' fill="currentColor"'
          : ' fill="none" stroke="currentColor" stroke-width="1.3"' +
            ' stroke-linejoin="round"') +
      '/></svg>';
  }

  /*
   * The done/not-done mark on a checklist somebody may only read.
   *
   * Not a disabled checkbox. A disabled control is skipped by keyboard
   * navigation and reads as an action that is unavailable, and neither is true
   * here - the state of an onboarding item is a fact about the record, not a
   * button the employee is briefly forbidden from pressing. Same 16px grid and
   * same currentColor reasoning as commentIcon.
   */
  function checklistStateIcon(done) {
    var ring = '<circle cx="8" cy="8" r="6.35" fill="none" stroke="currentColor"' +
      ' stroke-width="1.3"/>';
    var tick = '<circle cx="8" cy="8" r="7" fill="currentColor"/>' +
      '<path d="M4.6 8.3l2.2 2.2 4.6-4.6" fill="none" stroke="#fff"' +
        ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>';

    return '<svg class="item-state" viewBox="0 0 16 16" width="16" height="16"' +
      ' aria-hidden="true" focusable="false">' + (done ? tick : ring) + '</svg>';
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

  /*
   * The three document slots, for whichever side is asking.
   *
   * One function and not two, so the employee's view and HR's cannot drift on
   * what "uploaded" looks like. `editable` is the whole difference: true only for
   * an employee looking at their own live record. HR never gets it - they have no
   * upload route to call - and neither does an archived employee, whose record is
   * frozen server side.
   *
   * A slot arrives as {slot, label, uploaded} plus, when uploaded, {filename,
   * size, uploadedAt, downloadUrl}. Nothing is invented when a slot is empty:
   * there is no downloadUrl to link to, and this draws the empty state instead.
   */
  function documentsSection(documents, editable) {
    var cells = (documents || []).map(function (entry) {
      var body;

      if (entry.uploaded) {
        // slice(0, 10) because uploadedAt is a full ISO timestamp and formatDate
        // wants YYYY-MM-DD - handed the whole thing it returns "NaN Sep 2026".
        // archivedNotice does the same.
        var meta = formatBytes(entry.size) + ' \u00b7 ' +
          formatDate(String(entry.uploadedAt || '').slice(0, 10));

        body = '' +
          '<p class="doc-filename">' + escapeHtml(entry.filename) + '</p>' +
          '<p class="doc-meta">' + escapeHtml(meta) + '</p>' +
          // rel=noopener on a target=_blank link, always. Also note the download
          // is forced by Content-Disposition on the signed URL rather than by a
          // `download` attribute - that attribute is ignored cross-origin.
          '<p class="doc-actions">' +
            '<a href="' + escapeHtml(entry.downloadUrl) + '" target="_blank"' +
              ' rel="noopener">' + (editable ? 'View' : 'Download') + '</a>' +
          '</p>';
      } else {
        body = '<p class="doc-empty">' +
          (editable ? 'Nothing uploaded yet' : '\u2014 not uploaded \u2014') +
          '</p>';
      }

      if (!editable) {
        return '<div class="doc-slot">' +
          '<h3>' + escapeHtml(entry.label) + '</h3>' + body + '</div>';
      }

      // A label wrapping a visually hidden file input, rather than a button that
      // opens one. The label IS the drop zone, so the same element a mouse drags
      // onto is the one a keyboard reaches - and it needs no click handler at all,
      // because a label activating its input is native behaviour.
      var inputId = 'doc-file-' + entry.slot;

      return '' +
        '<div class="doc-slot" data-slot="' + escapeHtml(entry.slot) + '">' +
          '<h3>' + escapeHtml(entry.label) + '</h3>' +
          body +
          '<label class="doc-drop" for="' + inputId + '">' +
            '<span class="doc-drop-main">' +
              (entry.uploaded ? 'Replace this file' : 'Drop a file here') +
            '</span>' +
            '<span class="doc-drop-hint">or click to browse \u00b7 ' +
              'PDF, JPG, PNG or Word, up to 10 MB</span>' +
            // No `name`, deliberately. readForm() in js/app.js walks
            // form.elements and takes .value off anything named - and a file
            // input's value is the fake path "C:\fakepath\cv.pdf". This input
            // sits outside the contact form anyway; leaving the name off means it
            // stays harmless if that ever changes.
            '<input class="sr-only" type="file" id="' + inputId + '"' +
              ' data-slot="' + escapeHtml(entry.slot) + '"' +
              ' accept=".pdf,.jpg,.jpeg,.png,.docx">' +
          '</label>' +
          '<p class="doc-status" data-status-for="' +
            escapeHtml(entry.slot) + '" role="status"></p>' +
        '</div>';
    }).join('');

    var note = editable
      ? 'These are yours to upload. HR can see them but cannot change them.'
      : 'Uploaded by the employee. Read-only here \u2014 ask them to replace a ' +
        'wrong file.';

    /*
     * Three states, not two, and the distinction matters: the documents request
     * is no longer awaited before this view paints (see renderChecklist), so
     * "not here yet" is a normal condition and must not be reported as a
     * failure. null/undefined means still loading; an empty array means the
     * request finished and had nothing to give.
     */
    var body;
    if (!documents) {
      body = '<p class="doc-empty" role="status">Loading documents&hellip;</p>';
    } else if (!cells) {
      body = '<p class="doc-empty">Documents could not be loaded.</p>';
    } else {
      body = cells;
    }

    return '' +
      '<section id="documents">' +
        '<h2>' + (editable ? 'Your documents' : 'Documents') + '</h2>' +
        '<p class="view-note">' + note + '</p>' +
        '<div class="doc-grid">' + body + '</div>' +
      '</section>';
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
    } else if (config.type === 'textarea') {
      // Its own branch so the address box gets the same label, hint,
      // aria-describedby, aria-invalid and error slot every other control has -
      // which is the entire reason field() exists rather than hand-written markup.
      // readonly rather than disabled, for the reason spelled out below.
      control = '<textarea id="' + config.name + '" name="' + config.name + '"' +
        ' rows="' + (config.rows || 3) + '"' + shared +
        (config.maxlength ? ' maxlength="' + config.maxlength + '"' : '') +
        (config.readonly ? ' readonly' : '') +
        (config.placeholder ? ' placeholder="' + escapeHtml(config.placeholder) + '"' : '') +
        '>' + escapeHtml(config.value || '') + '</textarea>';
    } else {
      control = '<input id="' + config.name + '" name="' + config.name + '"' +
        ' type="' + (config.type || 'text') + '"' +
        ' value="' + escapeHtml(config.value || '') + '"' + shared +
        // readonly, never disabled. A disabled input is skipped by keyboard
        // navigation and is not read out, so the one field that explains why it
        // cannot be edited would be the one field nobody hears about.
        (config.readonly ? ' readonly' : '') +
        // Only the login fields set this. A password manager cannot offer to
        // fill or save a credential it has no way to identify.
        (config.autocomplete ? ' autocomplete="' + config.autocomplete + '"' : '') +
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
    formatBytes: formatBytes,
    documentsSection: documentsSection,

    /**
     * The HR landing page. Three cards, one per dashboard, each backed by its
     * own DynamoDB table now - Onboarding (people not yet finished),
     * Employee Tracking (promoted, non-intern staff) and Interns (promoted
     * interns, each linked to a reporting manager). No "coming soon" chips
     * any more - all three are live.
     */
    dashboardView: function () {
      return '' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">HR Dashboard</h1>' +
            '<p class="subtitle">Choose a section.</p>' +
          '</div>' +
        '</div>' +

        '<ul class="dash-grid">' +
          '<li>' +
            '<a class="dash-card" href="#/onboarding">' +
              '<h2>Onboarding</h2>' +
              '<p>New hires, checklists and documents.</p>' +
            '</a>' +
          '</li>' +
          '<li>' +
            '<a class="dash-card" href="#/interns">' +
              '<h2>Interns</h2>' +
              '<p>Onboarded interns and who they report to.</p>' +
            '</a>' +
          '</li>' +
          '<li>' +
            '<a class="dash-card" href="#/tracking">' +
              '<h2>Employee Tracking</h2>' +
              '<p>Onboarded employees.</p>' +
            '</a>' +
          '</li>' +
        '</ul>';
    },

    /**
     * The Interns dashboard - one card per intern reporting manager, each
     * listing their interns. `interns` and `employees` are both the full
     * lists from GET /staff/interns and GET /staff/employees - the manager
     * name/id for the "Reassign manager" picker comes from the latter.
     */
    internsView: function (interns, employees) {
      if (!interns.length) {
        return '' +
          '<a class="back-link" href="#/dashboard">&larr; Back to dashboard</a>' +
          '<div class="page-head">' +
            '<div>' +
              '<h1 tabindex="-1">Interns</h1>' +
              '<p class="subtitle">Onboarded interns and who they report to.</p>' +
            '</div>' +
          '</div>' +
          '<p class="empty-state">No interns have been moved here yet. Promote one from ' +
            'their checklist on the Onboarding dashboard.</p>';
      }

      var managerOptions = managerOptionsHtml(employees, null);

      return '' +
        '<a class="back-link" href="#/dashboard">&larr; Back to dashboard</a>' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">Interns</h1>' +
            '<p class="subtitle">' + recordCountLabel(interns.length, 'intern') + '</p>' +
          '</div>' +
        '</div>' +
        '<ul class="employee-grid" id="intern-cards">' +
          interns.map(function (intern) {
            return internCard(intern, managerOptions);
          }).join('') +
        '</ul>';
    },

    /**
     * The Employee Tracking dashboard - onboarded, non-intern staff. Reuses
     * the same card/grid markup the onboarding list uses (css/styles.css's
     * .employee-grid), rather than the placeholder .summary-grid this screen
     * used to render dummy stats into.
     */
    trackingView: function (employees) {
      if (!employees.length) {
        return '' +
          '<a class="back-link" href="#/dashboard">&larr; Back to dashboard</a>' +
          '<div class="page-head">' +
            '<div>' +
              '<h1 tabindex="-1">Employee Tracking</h1>' +
              '<p class="subtitle">Onboarded employees.</p>' +
            '</div>' +
          '</div>' +
          '<p class="empty-state">No one has been moved here yet. Promote a finished ' +
            'onboarding record from the Onboarding dashboard.</p>';
      }

      return '' +
        '<a class="back-link" href="#/dashboard">&larr; Back to dashboard</a>' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">Employee Tracking</h1>' +
            '<p class="subtitle">' + recordCountLabel(employees.length, 'employee') + '</p>' +
          '</div>' +
        '</div>' +
        '<ul class="employee-grid" id="staff-employee-cards">' +
          employees.map(staffEmployeeCard).join('') +
        '</ul>';
    },

    /**
     * The sign-in screen. `error` is the message from a rejected attempt, or
     * null on first paint.
     *
     * Uses field() like the employee form so the label wiring, the hint and the
     * error slot behave identically - a login page that invents its own markup
     * is a login page whose error message is the one thing nobody hears.
     */
    loginView: function (error) {
      return '' +
        '<div class="login-card">' +
          '<h1 tabindex="-1">Sign in</h1>' +
          '<p class="subtitle">Employee onboarding at Breville.</p>' +

          // role="alert" so the message is announced when it appears. It sits
          // inside the card rather than in the page-level banner because it
          // belongs to these two inputs - and the banner is cleared on every
          // render, which is exactly when a failed login repaints.
          (error
            ? '<p class="login-error" role="alert">' + escapeHtml(error) + '</p>'
            : '') +

          '<form class="form" id="login-form" novalidate>' +
            field({
              name: 'username',
              label: 'Username',
              required: true,
              // Because the two roles do not sign in with the same kind of thing.
              // HR has an account; an employee has a number, and nothing on this
              // page would otherwise say so.
              hint: 'HR signs in with a username. Employees use their employee number.',
              // Tells a password manager which field is which. Without it, the
              // browser cannot offer to fill or save either one.
              autocomplete: 'username'
            }) +
            field({
              name: 'password',
              label: 'Password',
              type: 'password',
              required: true,
              autocomplete: 'current-password'
            }) +
            '<div class="form-actions">' +
              '<button class="btn btn-primary" type="submit" id="login-submit">Sign in</button>' +
            '</div>' +
          '</form>' +

          // There is no sign-up and no password reset, so the accounts have to
          // be discoverable from the page itself. See src/common/accounts.py.
          '<div class="login-hint">' +
            '<p><strong>Demo accounts</strong></p>' +
            '<p>Officials &mdash; <code>hr.admin</code> / <code>onboard-2026</code><br>' +
            'Employee &mdash; your employee number, e.g. <code>E1001</code> / ' +
              '<code>welcome-2026</code></p>' +
          '</div>' +
        '</div>';
    },

    /**
     * The employee's own record - the whole employee side of the app, in one
     * screen.
     *
     * It replaced a directory of everybody. The shape of that change is worth
     * knowing when reading this: nothing here filters or hides a field, because
     * the server already sent exactly what this role may see
     * (own_profile_view in common/models.py). What this file decides is which
     * parts are *editable*, and the API refuses the rest independently - so a
     * control added here by mistake produces a 403, not an edit.
     *
     * Four sections, in the order somebody actually wants them: how far along am
     * I, what does the company have on file, what is outstanding, and what do you
     * need from me.
     */
    profileView: function (employee, documents) {
      var p = employee.progress;
      // Archived records are frozen server side. Offering live inputs over one
      // would present an action that can only ever fail with a 409.
      var frozen = !!employee.archived;

      var items = employee.checklist.map(function (item) {
        return '' +
          '<li class="' + (item.done ? 'done' : '') + '">' +
            '<div class="item-row">' +
              checklistStateIcon(item.done) +
              // The icon is aria-hidden, so the state has to be said in words
              // somewhere or the list reads as eight labels and no answers.
              '<span class="sr-only">' + (item.done ? 'Done' : 'Not done yet') +
                '</span>' +
              '<span class="item-label">' + escapeHtml(item.label) + '</span>' +
              // Kept, and it is the most useful thing on the row: it says who
              // the item is waiting on, which is sometimes them.
              '<span class="owner">' + escapeHtml(item.owner) + '</span>' +
            '</div>' +
          '</li>';
      }).join('');

      var detail = function (label, value) {
        return '<div><dt>' + escapeHtml(label) + '</dt><dd>' +
          escapeHtml(value || '-') + '</dd></div>';
      };

      return '' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">' + escapeHtml(fullName(employee)) + '</h1>' +
            '<p class="subtitle">' + escapeHtml(employee.jobTitle) + ' &middot; ' +
              escapeHtml(employee.department) + '</p>' +
          '</div>' +
        '</div>' +

        archivedNotice(employee) +

        '<div class="summary-card">' +
          statusBadge(employee) +
          ' <strong id="progress-text">' + p.done + ' of ' + p.total +
            ' complete</strong>' +
          '<div class="progress-track" style="margin-top:8px" role="progressbar"' +
            ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + p.percent + '"' +
            ' aria-labelledby="progress-text">' +
            '<div class="progress-bar" style="width:' + p.percent + '%"></div>' +
          '</div>' +
          '<dl class="summary-grid">' +
            detail('Employee ID', employee.id) +
            detail('Department', employee.department) +
            detail('Job title', employee.jobTitle) +
            detail('Start date', formatDate(employee.startDate)) +
            detail('Reporting manager', employee.manager) +
            detail('Employment type', employee.employmentType) +
            detail('Work email', employee.email) +
          '</dl>' +
          '<p class="view-note">HR maintains the details above. Ask them if any ' +
            'of it needs correcting &mdash; an employee number can never be ' +
            'changed at all.</p>' +
        '</div>' +

        '<h2>Your onboarding checklist</h2>' +
        '<p class="view-note">HR and IT tick these off as they go. The owner ' +
          'beside each one says who it is waiting on.</p>' +
        '<ul class="checklist readonly">' + items + '</ul>' +

        '<h2>Your details</h2>' +
        '<p class="view-note">' + (frozen
          ? 'This record is archived, so these can no longer be changed.'
          : 'These three are yours to fill in and to keep up to date.') + '</p>' +
        '<form class="form" id="contact-form" novalidate>' +
          '<div class="form-grid">' +
            field({
              name: 'phone',
              label: 'Phone',
              value: employee.phone,
              readonly: frozen,
              placeholder: '+61 4XX XXX XXX'
            }) +
            field({
              name: 'personalEmail',
              label: 'Personal email',
              type: 'email',
              value: employee.personalEmail,
              readonly: frozen,
              hint: 'Somewhere we can reach you before your work account exists.'
            }) +
            field({
              name: 'address',
              label: 'Home address',
              type: 'textarea',
              rows: 3,
              maxlength: 300,
              full: true,
              value: employee.address,
              readonly: frozen
            }) +
          '</div>' +
          (frozen
            ? ''
            : '<div class="btn-row">' +
                '<button class="btn btn-primary" type="submit">Save details</button>' +
              '</div>') +
        '</form>' +

        // Editable only on a live record. An archived one is frozen server side,
        // so its slots render as plain text rather than as drop zones that could
        // only ever produce a 409.
        documentsSection(documents, !frozen);
    },

    /**
     * Stands in for profileView when the signed-in number has no record behind it.
     *
     * Not notFoundView: that one offers a link back to #/onboarding, which the
     * employee guard bounces straight back here - a dead end that looks like a
     * broken app. This is reachable in two ordinary ways, so it says what to do
     * about both: a mistyped employee number (POST /login cannot check one
     * exists - it has no table access, by design), and a session left open from
     * before this screen existed.
     */
    profileMissingView: function (employeeId) {
      return '' +
        '<div class="message-card">' +
          '<h1 tabindex="-1">We cannot find your record</h1>' +
          '<p>Nothing is on file under <code>' + escapeHtml(employeeId || '-') +
            '</code>.</p>' +
          '<p>If that is not your employee number, sign out and sign in again ' +
            'with the right one. If it is, HR has not added your record yet ' +
            '&mdash; they will need to create it before this page can show you ' +
            'anything.</p>' +
        '</div>';
    },

    /**
     * One card per employee, as <li>s for the <ul> that listView renders.
     *
     * This replaced a seven-column table. The table was the more honest
     * structure for tabular data and it is worth being clear about what was
     * traded away: a screen reader no longer announces "Department, Engineering"
     * as it moves across a row, because there are no longer columns to name. So
     * the card states each fact in words instead - the job title and department
     * read as one sentence, the start date carries its own "Starts" label - and
     * the list is a <ul> so the count and the boundaries between records are
     * still announced.
     */
    employeeCards: function (employees) {
      if (!employees.length) {
        return '<li class="employee-empty">' +
          '<p class="empty-state">No employees match this view.</p></li>';
      }

      return employees.map(function (employee) {
        // "Edit" six times over tells a screen-reader user nothing about which
        // record they are on, so every control in the card carries the name.
        var name = escapeHtml(fullName(employee));
        var href = '#/onboarding/' + encodeURIComponent(employee.id);

        return '' +
          '<li class="employee-card" data-id="' + escapeHtml(employee.id) + '">' +
            '<div class="ec-top">' +
              '<span class="ec-id">' + escapeHtml(employee.id) + '</span>' +
              statusBadge(employee) +
              // Onboarded and not yet promoted - a nudge to open the
              // checklist and use the "Move to..." button there, which is
              // the one place that control lives.
              (employee.status === 'Onboarded'
                ? '<span class="dash-card-chip">Ready to move</span>'
                : '') +
            '</div>' +

            '<div>' +
              /*
               * The name is a real <a>, and styles.css stretches its ::after
               * over the whole card - so the entire card is the click target
               * for "open this checklist", which is what the hover state has
               * always implied. A real link and not a click handler on the
               * <li>, so middle-click, ctrl-click, right-click -> copy address
               * and tabbing to it all behave the way they look like they
               * should. The employee's name IS the link text, which is the
               * label a screen reader wants anyway.
               */
              '<h3 class="ec-name">' +
                '<a class="ec-link" href="' + href + '/checklist">' + name + '</a>' +
              '</h3>' +
              '<p class="ec-role">' + escapeHtml(employee.jobTitle) +
                ' &middot; ' + escapeHtml(employee.department) + '</p>' +
            '</div>' +

            '<p class="ec-email">' + escapeHtml(employee.email) + '</p>' +

            progressBlock(employee, 'list') +

            '<p class="ec-start">' +
              '<span class="ec-start-label">Starts</span>' +
              escapeHtml(formatDate(employee.startDate)) +
            '</p>' +

            // No "Checklist" link any more - the card itself is that link now,
            // and two controls for one destination is one too many. These two
            // sit above the stretched overlay; see .ec-foot in styles.css.
            '<div class="ec-foot">' +
              '<a class="btn-link" href="' + href + '/edit"' +
                ' aria-label="Edit ' + name + '">Edit</a>' +
              '<button class="btn-link danger" type="button" data-action="delete"' +
                ' aria-label="Delete ' + name + '">Delete</button>' +
            '</div>' +
          '</li>';
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
        '<a class="back-link" href="#/dashboard">&larr; Back to dashboard</a>' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">Employees</h1>' +
            // Polite, so re-filtering as you type announces the new count
            // without cutting off whatever is being read.
            '<p class="subtitle" id="record-count" aria-live="polite"></p>' +
          '</div>' +
          '<a class="btn btn-primary" href="#/onboarding/new">Add Employee</a>' +
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

        // A list, not a table - the column count is what used to force a
        // horizontal scroll on anything narrower than a laptop, and the grid in
        // styles.css reflows from three columns to one on its own. <ul> rather
        // than a bare set of divs so the number of records and the boundary
        // between them are still announced.
        '<ul class="employee-grid" id="employee-cards"></ul>';
    },

    formView: function (employee, facets) {
      var isEdit = !!employee;
      var data = employee || {};

      return '' +
        '<a class="back-link" href="#/onboarding">&larr; Back to employees</a>' +
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
            '<a class="btn" href="#/onboarding">Cancel</a>' +
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
    checklistView: function (employee, editing, documents, managers) {
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
        '<a class="back-link" href="#/onboarding">&larr; Back to employees</a>' +
        '<div class="page-head">' +
          '<div>' +
            '<h1 tabindex="-1">' + escapeHtml(fullName(employee)) + '</h1>' +
            '<p class="subtitle">' + escapeHtml(employee.jobTitle) + ' &middot; ' +
              escapeHtml(employee.department) + '</p>' +
          '</div>' +
          (frozen
            ? ''
            : '<a class="btn" href="#/onboarding/' + encodeURIComponent(employee.id) +
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

        promoteSection(employee, managers === undefined ? null : managers) +

        '<h2>Onboarding checklist</h2>' +
        '<ul class="checklist" id="checklist">' + items + '</ul>' +

        // Read-only, always. There is no officials upload route to call - see
        // handlers/request_document_upload - so this cannot offer one.
        documentsSection(documents, false);
    },

    /*
     * Stands in for the edit form on an archived employee. Not notFoundView:
     * the record is right there and readable, it just cannot be edited, and
     * "does not exist" would send someone looking for a record that does.
     */
    archivedView: function (employee) {
      return '' +
        '<a class="back-link" href="#/onboarding">&larr; Back to employees</a>' +
        '<h1 tabindex="-1">' + escapeHtml(fullName(employee)) + '</h1>' +
        '<p class="subtitle">' + escapeHtml(employee.jobTitle) + ' &middot; ' +
          escapeHtml(employee.department) + '</p>' +
        archivedNotice(employee) +
        '<p><a class="btn" href="#/onboarding/' + encodeURIComponent(employee.id) +
          '/checklist">View onboarding record</a></p>';
    },

    notFoundView: function () {
      return '' +
        '<a class="back-link" href="#/onboarding">&larr; Back to employees</a>' +
        '<h1 tabindex="-1">Not found</h1>' +
        '<p class="subtitle">That employee record does not exist.</p>';
    },

    /* --------------------------------------------------- request feedback */

    loadingView: function () {
      // role=status so the wait itself is announced; app.js pairs this with
      // aria-busy on #app.
      return '<p class="empty-state" role="status">Loading&hellip;</p>';
    },

    /**
     * The checklist page, as grey bars, for the one path that actually waits:
     * a deep link or a hard refresh, where there is no cached record to paint.
     * Arriving from the list skips this entirely.
     *
     * The bars are aria-hidden and a single sr-only line carries the state, so
     * this announces "Loading" once rather than reading out a dozen empty divs.
     */
    checklistSkeleton: function () {
      var rows = '';
      for (var i = 0; i < 5; i += 1) {
        rows += '<div class="sk-row"></div>';
      }

      return '' +
        '<p class="sr-only" role="status">Loading this employee&rsquo;s record&hellip;</p>' +
        '<div class="skeleton" aria-hidden="true">' +
          '<div class="sk-bar sk-title"></div>' +
          '<div class="sk-bar sk-subtitle"></div>' +
          '<div class="sk-card"></div>' +
          '<div class="sk-bar sk-heading"></div>' +
          '<div class="sk-list">' + rows + '</div>' +
        '</div>';
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
    },

    /**
     * The visible half of a write confirmation - "Changes saved.", "Document
     * uploaded." - for the actions that have no other on-screen sign that they
     * worked. Same shape as errorBanner so the two behave identically; only the
     * colour and the data-action differ, so dismissing one never eats the other.
     */
    successBanner: function (message) {
      return '' +
        '<div class="notice">' +
          '<span>' + escapeHtml(message) + '</span>' +
          '<button type="button" class="btn-link" data-action="dismiss-notice">Dismiss</button>' +
        '</div>';
    }
  };
})(window.App);
