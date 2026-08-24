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
  var statusSlot = document.getElementById('app-status');
  var store = App.store;
  var ui = App.ui;

  var BASE_TITLE = 'Employee Management & Onboarding';

  /* --------------------------------------------------------- announcements */

  /*
   * Every view here is built by replacing the innerHTML of #app, which is
   * invisible to assistive tech: no page load happens, so nothing is announced
   * and focus stays on whatever the user just activated - a link that no longer
   * exists. Three things fix that, and all three are cheap:
   *
   *   paint()         swaps the view and clears aria-busy
   *   focusHeading()  moves focus to the new view's <h1>, so the next Tab starts
   *                   inside the content that just arrived
   *   announce()      writes to a live region that lives OUTSIDE #app, because a
   *                   live region inserted at the same moment as its text is
   *                   usually not announced at all
   */

  function paint(html) {
    root.innerHTML = html;
    root.removeAttribute('aria-busy');
  }

  function focusHeading() {
    var heading = root.querySelector('h1');
    if (heading) heading.focus();
  }

  function announce(text) {
    statusSlot.textContent = text;
  }

  /** The title is how a tab, a history entry and a screen reader all name the view. */
  function setTitle(text) {
    document.title = text ? text + ' \u00b7 ' + BASE_TITLE : BASE_TITLE;
  }

  // List filters survive navigation, so returning from a checklist keeps the view.
  var filters = { search: '', department: '', status: '' };
  var loadedEmployees = [];

  /*
   * The values the dropdowns offer, collected from the employees the API
   * returned rather than from a list held here. There is no enum in js/ any
   * more: the departments and employment types this app accepts are defined
   * once, in common/models.py, and duplicating them client-side is what
   * produced the last mismatch between the two.
   *
   * The trade is visible and worth stating: a department nobody is in yet is not
   * offered, and on an empty table the form falls back to plain text inputs
   * (see field() in ui.js). Either way the server has the real list and rejects
   * anything that is not on it, with the message landing under the input.
   */
  var facets = { departments: [], employmentTypes: [], statuses: [] };

  function unique(values) {
    var seen = {};
    return values.filter(function (value) {
      if (!value || seen[value]) return false;
      seen[value] = true;
      return true;
    });
  }

  function pluck(field) {
    return function (employee) { return employee[field]; };
  }

  /*
   * Statuses sorted by the lowest progress percentage seen carrying them, which
   * puts Pending before In Progress before Onboarded without this file knowing
   * that those are the three or what they mean. Alphabetical would read as
   * "In Progress, Onboarded, Pending".
   */
  function orderedStatuses(employees) {
    var lowest = {};

    employees.forEach(function (employee) {
      if (!employee.status) return;
      var percent = (employee.progress || {}).percent || 0;
      if (!(employee.status in lowest) || percent < lowest[employee.status]) {
        lowest[employee.status] = percent;
      }
    });

    return Object.keys(lowest).sort(function (a, b) { return lowest[a] - lowest[b]; });
  }

  function setEmployees(employees) {
    loadedEmployees = employees;
    facets = {
      departments: unique(employees.map(pluck('department'))).sort(),
      employmentTypes: unique(employees.map(pluck('employmentType'))).sort(),
      statuses: orderedStatuses(employees)
    };

    // A filter pinned to a value that no longer exists - the last person in
    // Finance was deleted - would show an empty table with a blank dropdown and
    // no way to tell why. Drop it instead.
    if (facets.departments.indexOf(filters.department) === -1) filters.department = '';
    if (facets.statuses.indexOf(filters.status) === -1) filters.status = '';
  }

  /**
   * The form routes need the facets, and a deep link to #/employees/new lands
   * without the list ever having been fetched. Cached, because these are only
   * the dropdown values - the list view itself always refetches.
   */
  function ensureEmployees() {
    if (loadedEmployees.length) return Promise.resolve(loadedEmployees);
    return store.listEmployees().then(function (employees) {
      setEmployees(employees);
      return employees;
    });
  }

  /**
   * An employee's own department must stay selectable while editing them, even
   * if they are the only one in it and the list has not been loaded since.
   */
  function formFacets(employee) {
    if (!employee) return facets;
    return {
      departments: unique(facets.departments.concat(employee.department)).sort(),
      employmentTypes: unique(facets.employmentTypes.concat(employee.employmentType)).sort(),
      statuses: facets.statuses
    };
  }

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
      paint(ui.messageView(context));
      focusHeading();
    };
  }

  function showLoading() {
    // aria-busy tells a screen reader the region is mid-update, so it waits for
    // the real view instead of reading the placeholder as the answer.
    root.setAttribute('aria-busy', 'true');
    root.innerHTML = ui.loadingView();
  }

  errorSlot.addEventListener('click', function (event) {
    if (event.target.closest('[data-action="dismiss-error"]')) clearError();
  });

  // Escape dismisses the banner, which until now was reachable only by finding
  // and clicking its button.
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && errorSlot.firstChild) clearError();
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

    // The live region is deliberately NOT cleared here. "Employee added." is
    // announced by the save, which then navigates - and clearing on arrival wiped
    // the message before a screen reader ever reached it. Stale text is harmless:
    // a live region speaks when its contents change, not because they are there.

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
      if (filters.status && employee.status !== filters.status) return false;
      if (!term) return true;

      var haystack = (ui.fullName(employee) + ' ' + employee.email).toLowerCase();
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
      setEmployees(employees);
      setTitle('Employees');
      paint(ui.listView(employees, filters, facets));
      refreshRows();
      focusHeading();

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

        // Says what actually happens now. "Delete" would be a lie: the record
        // and its checklist survive, they just stop being listed here.
        if (!window.confirm('Remove ' + ui.fullName(employee) + ' from the employee list?' +
            '\n\nTheir record and onboarding history are kept, and can no longer be edited.')) return;

        clearError();
        button.disabled = true;

        store.archiveEmployee(id).then(function (archived) {
          // Said out loud, because the row simply vanishing is not an event a
          // screen reader reports. The state comes off the response rather than
          // being recomputed here - the server owns that rule.
          announce(ui.fullName(employee) + ' was removed from the list and marked ' +
            archived.archivedAs + '.');
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

      // The red border is the only signal a sighted user needs and the only one
      // nobody else gets. aria-invalid is the same fact, said in a way a screen
      // reader repeats when the field is focused.
      var control = form.elements[name];
      if (control) control.setAttribute('aria-invalid', message ? 'true' : 'false');
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
    // Any status, not just 400: a field-specific problem can arrive with any code,
    // and it belongs under its input rather than in the banner.
    if (error.fields) {
      showErrors(form, error.fields);
      return;
    }
    showError(error);
  }

  function renderForm(id) {
    // Both forms wait now, the add form included: its dropdowns are built from
    // the employees the API returns, so there is nothing to render until that
    // list is in.
    showLoading();

    var load = Promise.all([
      id ? store.getEmployee(id) : null,
      ensureEmployees()
    ]).then(function (results) { return results[0]; });

    load.then(function (employee) {
      if (id && !employee) {
        setTitle('Not found');
        paint(ui.notFoundView());
        focusHeading();
        return;
      }

      // An archived record is read-only server side, so rendering an editable
      // form over it would only produce a 409 on save. Reachable by URL or a
      // stale bookmark, since archiving is what took it off the list.
      if (employee && employee.archived) {
        setTitle(ui.fullName(employee) + ' \u2014 archived');
        paint(ui.archivedView(employee));
        focusHeading();
        return;
      }

      setTitle(employee ? 'Edit ' + ui.fullName(employee) : 'Add Employee');
      paint(ui.formView(employee, formFacets(employee)));
      focusHeading();
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
          announce(employee ? 'Changes saved.' : 'Employee added.');
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
          if (!window.confirm('Remove ' + ui.fullName(employee) + ' from the employee list?' +
              '\n\nTheir record and onboarding history are kept, and can no longer be edited.')) return;

          clearError();
          deleteButton.disabled = true;

          store.archiveEmployee(employee.id).then(function (archived) {
            announce(ui.fullName(employee) + ' was removed from the list and marked ' +
              archived.archivedAs + '.');
            navigate('#/employees');
          }, function (error) {
            // The form is still filled in and still valid; keep it usable.
            deleteButton.disabled = false;
            showError(error);
          });
        });
      }
    }, failLoad(id ? 'Could not load this employee.' : 'Could not load the form.'));
  }

  /* -------------------------------------------------------- checklist view */

  /*
   * The one comment box that is open, as { itemId, draft }, or null.
   *
   * Kept here rather than read out of the DOM because every tick repaints the
   * whole view from the server's response - so a comment being typed has to be
   * re-rendered into the new DOM, not left behind in the old one.
   */
  var commentEditor = null;

  function checklistItem(employee, itemId) {
    return employee.checklist.filter(function (item) {
      return item.id === itemId;
    })[0];
  }

  function renderChecklist(id) {
    showLoading();
    commentEditor = null;

    store.getEmployee(id).then(function (employee) {
      if (!employee) {
        setTitle('Not found');
        paint(ui.notFoundView());
        focusHeading();
        return;
      }
      setTitle(ui.fullName(employee) + ' \u2014 checklist');
      paintChecklist(employee);
    }, failLoad('Could not load this checklist.'));
  }

  /*
   * Split out from renderChecklist so a tick can repaint from the PATCH response
   * instead of triggering a second GET - and so the repaint costs no placeholder
   * flash on every click.
   */
  /**
   * `focus` says what to put the cursor back on after the repaint:
   * { kind: 'checkbox' | 'button' | 'editor', itemId }, or null for the heading.
   */
  function paintChecklist(employee, focus) {
    paint(ui.checklistView(employee, commentEditor));

    /*
     * A tick replaces the whole view, which throws away the control the user is
     * standing on - so a keyboard user is dumped back to the top of the document
     * on every single tick, and there is no way to work down the list. Put focus
     * back where it was; only a fresh arrival gets the heading.
     */
    restoreFocus(focus);
    wireComments(employee);

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
        paintChecklist(updated, { kind: 'checkbox', itemId: itemId });
        announceTick(updated, itemId);
      }, function (error) {
        // The write did not land, so put the box back to what the server still
        // believes. We deliberately don't re-fetch to confirm that - the request
        // that would tell us is the one that just failed.
        checkbox.checked = !intended;
        checkbox.disabled = false;

        // Disabling an element that has focus hands focus to <body>, so the
        // round trip above quietly dropped a keyboard user out of the list.
        // Only take it back if that is what happened - if they have since
        // clicked or tabbed somewhere else, leave them there.
        if (document.activeElement === document.body) checkbox.focus();

        var label = checkbox.closest('label').querySelector('.item-label');
        announce('Could not save that change. ' +
          (label ? label.textContent : 'That item') + ' was left as it was.');
        showError(error);
      });
    });
  }

  function restoreFocus(focus) {
    if (!focus) return focusHeading();

    var selectors = {
      checkbox: 'input[data-item-id="' + focus.itemId + '"]',
      button: '[data-action="comment"][data-item-id="' + focus.itemId + '"]',
      editor: '.comment-editor textarea'
    };

    var target = root.querySelector(selectors[focus.kind]);
    if (!target) return focusHeading();

    target.focus();
    // Land at the end of what is already written rather than in front of it -
    // an editor opens to add to a note, not to overwrite it.
    if (focus.kind === 'editor' && target.setSelectionRange) {
      target.setSelectionRange(target.value.length, target.value.length);
    }
  }

  /* -------------------------------------------------- checklist comments */

  function wireComments(employee) {
    var list = document.getElementById('checklist');

    list.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-action]');
      if (!button) return;

      var action = button.getAttribute('data-action');
      var itemId = button.getAttribute('data-item-id') ||
        (commentEditor && commentEditor.itemId);
      if (!itemId) return;

      if (action === 'comment') toggleEditor(employee, itemId);
      if (action === 'cancel-comment') closeEditor(employee);
      if (action === 'save-comment') saveComment(employee, itemId, currentDraft());
      if (action === 'delete-comment') saveComment(employee, itemId, '');
    });

    // Every keystroke goes into the state object, so the next repaint - which a
    // tick on any other item will cause - re-renders the text rather than
    // silently discarding it.
    list.addEventListener('input', function (event) {
      if (commentEditor && event.target.matches('.comment-editor textarea')) {
        commentEditor.draft = event.target.value;
      }
    });

    list.addEventListener('keydown', function (event) {
      if (!event.target.matches('.comment-editor textarea')) return;

      if (event.key === 'Escape') {
        // Stop it reaching the document handler, which would dismiss the error
        // banner instead - two meanings for one key, and this is the nearer one.
        event.stopPropagation();
        closeEditor(employee);
      } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        saveComment(employee, commentEditor.itemId, currentDraft());
      }
    });
  }

  function currentDraft() {
    var textarea = root.querySelector('.comment-editor textarea');
    return textarea ? textarea.value : (commentEditor ? commentEditor.draft : '');
  }

  /**
   * The icon opens the box and closes it again - it is the same control, so a
   * second press has to mean "put this away" rather than "reopen and reset",
   * which is what it used to do (silently replacing anything typed with the
   * saved text).
   */
  function toggleEditor(employee, itemId) {
    if (commentEditor && commentEditor.itemId === itemId) {
      if (!mayDiscardDraft(employee)) return;
      closeEditor(employee, 'Comment box closed.');
      return;
    }
    openEditor(employee, itemId);
  }

  /*
   * Cancel and Esc say "discard" in as many words, so they just do it. The icon
   * does not, and neither does clicking the icon on a different item - so those
   * two ask, and only when there is actually something to lose.
   */
  function mayDiscardDraft(employee) {
    if (!commentEditor) return true;

    var item = checklistItem(employee, commentEditor.itemId);
    var unsaved = currentDraft() !== ((item && item.comment) || '');
    return !unsaved || window.confirm('Discard the comment you were writing?');
  }

  function openEditor(employee, itemId) {
    var item = checklistItem(employee, itemId);
    if (!item) return;

    // Only one box is open at a time, so opening this one closes that one.
    if (!mayDiscardDraft(employee)) return;

    clearError();
    commentEditor = { itemId: itemId, draft: item.comment || '' };
    paintChecklist(employee, { kind: 'editor', itemId: itemId });
    announce('Editing the comment on ' + item.label + '.');
  }

  function closeEditor(employee, message) {
    if (!commentEditor) return;

    var itemId = commentEditor.itemId;
    commentEditor = null;
    // Repaint from the employee we already have: nothing was written, so there
    // is nothing to re-read.
    paintChecklist(employee, { kind: 'button', itemId: itemId });

    // The box collapsing is visible on screen and silent everywhere else.
    if (message) announce(message);
  }

  function saveComment(employee, itemId, text) {
    var item = checklistItem(employee, itemId);
    var editor = root.querySelector('.comment-editor');
    var saveButton = editor && editor.querySelector('[data-action="save-comment"]');

    clearError();
    if (saveButton) {
      saveButton.disabled = true;
      saveButton.textContent = 'Saving\u2026';
    }

    store.setChecklistComment(employee.id, itemId, text).then(function (updated) {
      commentEditor = null;
      paintChecklist(updated, { kind: 'button', itemId: itemId });
      announce(text
        ? 'Comment saved on ' + item.label + '.'
        : 'Comment removed from ' + item.label + '.');
    }, function (error) {
      // Deliberately no repaint: the draft is still in the textarea and the
      // whole point of failing is not to lose it.
      if (saveButton) {
        saveButton.disabled = false;
        saveButton.textContent = 'Save comment';
      }

      var inline = editor && editor.querySelector('[data-error-for="comment"]');
      if (error.fields && error.fields.comment && inline) {
        inline.textContent = error.fields.comment;
        return;   // a problem with this one field belongs under this one field
      }
      showError(error);
    });
  }

  /*
   * The progress bar and the status badge both change on a tick, and neither is
   * near the checkbox. Say what happened instead: what was ticked, and where
   * that leaves the checklist.
   */
  function announceTick(employee, itemId) {
    var item = employee.checklist.filter(function (entry) {
      return entry.id === itemId;
    })[0];
    if (!item) return;

    announce(item.label + (item.done ? ' ticked. ' : ' unticked. ') +
      employee.progress.done + ' of ' + employee.progress.total + ' complete. ' +
      employee.status + '.');
  }

  /* ------------------------------------------------------------- bootstrap */

  window.addEventListener('hashchange', render);

  if (!window.location.hash) {
    window.location.hash = '#/employees';
  } else {
    render();
  }
})(window.App);
