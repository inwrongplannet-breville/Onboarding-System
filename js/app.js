/**
 * Router, event wiring and form validation.
 *
 * Routes (hash-based, so views are linkable and the back button works):
 *   #/login
 *   #/me                         employee role - their own record, and only theirs
 *   #/dashboard                  officials role - the HR dashboard, and their landing page
 *   #/interns                    officials role - interns sub-dashboard (scaffold)
 *   #/tracking                   officials role - employee tracking sub-dashboard (dummy data)
 *   #/attendance                 officials role - monthly attendance sheet
 *   #/onboarding                 officials role - the three routes below
 *   #/onboarding/new
 *   #/onboarding/:id/edit
 *   #/onboarding/:id/checklist
 *
 * Every route but #/login is behind guard(), which is a convenience and not a
 * control. The control is server-side: an employee who edits their way past the
 * guard reaches an officials screen whose every request comes back 403. See
 * js/auth.js.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  var root = document.getElementById('app');
  var errorSlot = document.getElementById('app-error');
  var noticeSlot = document.getElementById('app-notice');
  var statusSlot = document.getElementById('app-status');
  var store = App.store;
  var ui = App.ui;
  var auth = App.auth;

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

  var noticeTimer = null;

  /*
   * The visible counterpart to announce(), for the handful of writes that have
   * no other on-screen sign that they worked (a contact-form save, an
   * archive, a comment). Not called alongside announce() for the same
   * message - that would speak it to a screen reader twice, once from each
   * live region - so a call site uses one or the other, never both.
   */
  function notify(text) {
    noticeSlot.innerHTML = ui.successBanner(text);
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(clearNotice, 4000);
  }

  function clearNotice() {
    noticeSlot.innerHTML = '';
    clearTimeout(noticeTimer);
    noticeTimer = null;
  }

  noticeSlot.addEventListener('click', function (event) {
    if (event.target.closest('[data-action="dismiss-notice"]')) clearNotice();
  });

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
   * The form routes need the facets, and a deep link to #/onboarding/new lands
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

  /* ----------------------------------------------------------------- theme */

  /*
   * Light or dark, chosen here and nowhere else.
   *
   * The attribute on <html> is the single source of truth - css/styles.css
   * declares every colour twice, once on :root and once under
   * [data-theme="dark"], so flipping it repaints the whole app. This function
   * does not set it on first load: the inline script in index.html already did
   * that, before the stylesheet painted, which is what stops a dark-theme user
   * seeing a white flash on every navigation.
   *
   * prefers-color-scheme is deliberately not consulted. Light is the default
   * and the toggle is the only thing that changes it, so what you last picked
   * is what you get on the next visit - including when that differs from the OS.
   */

  var themeButton = document.getElementById('theme-toggle');
  var THEME_KEY = 'theme';

  function isDark() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  }

  /*
   * Rewrites the button to describe the press rather than the state. An icon
   * alone cannot say which of the two it means, and "Dark theme" as a label is
   * ambiguous between "you are in it" and "this takes you to it".
   */
  function paintTheme() {
    if (!themeButton) return;

    var dark = isDark();
    var label = dark ? 'Switch to light theme' : 'Switch to dark theme';

    themeButton.setAttribute('aria-pressed', dark ? 'true' : 'false');
    themeButton.setAttribute('aria-label', label);
    themeButton.setAttribute('title', label);
  }

  function setTheme(dark) {
    if (dark) {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      // Removed rather than set to "light": the absence of the attribute is
      // what :root already styles, so there is only one way to be in light
      // mode instead of two that have to be kept in agreement.
      document.documentElement.removeAttribute('data-theme');
    }

    try {
      window.localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
    } catch (e) {
      // Private mode, or storage full. The theme still applies for this page
      // view; it just will not survive a reload, which is a better outcome
      // than refusing to switch at all.
    }

    paintTheme();
    announce(dark ? 'Dark theme on.' : 'Light theme on.');
  }

  if (themeButton) {
    themeButton.addEventListener('click', function () {
      setTheme(!isDark());
    });
  }

  paintTheme();

  /* ------------------------------------------------------------ session chip */

  /*
   * Who is signed in, and the way out. Lives in the page header rather than in
   * #app because every render replaces #app wholesale, and a sign-out button
   * that disappears for the duration of a fetch is a sign-out button people
   * click twice.
   */

  var sessionSlot = document.getElementById('session-chip');
  var brandLink = document.querySelector('.brand');

  /** "Jordan Lee" -> "JL". Up to two words, so an employee number with no
    * space yet (a stale display name) still yields one legible letter. */
  function initials(name) {
    var letters = (name || '').trim().split(/\s+/).slice(0, 2).map(function (word) {
      return word.charAt(0).toUpperCase();
    });
    return letters.join('');
  }

  function paintSession() {
    var session = auth.session();

    if (!session) {
      sessionSlot.innerHTML = '';
      // The logo has to point somewhere the guard will not bounce, or clicking
      // it from the login page flickers through a redirect back to itself.
      if (brandLink) brandLink.setAttribute('href', '#/login');
      return;
    }

    if (brandLink) brandLink.setAttribute('href', auth.home());

    sessionSlot.innerHTML = '' +
      '<span class="session-avatar" aria-hidden="true">' +
        ui.escapeHtml(initials(session.displayName)) + '</span>' +
      '<span class="session-name">' + ui.escapeHtml(session.displayName) +
        // Says which of the two views they are in, because the difference
        // between them is mostly things that are absent - and an employee who
        // cannot find the Add button should be able to see why.
        (auth.isEmployee() ? ' <span class="session-role">my profile</span>' : '') +
      '</span>' +
      '<button class="btn-link" type="button" data-action="sign-out">Sign out</button>';
  }

  sessionSlot.addEventListener('click', function (event) {
    if (!event.target.closest('[data-action="sign-out"]')) return;

    auth.signOut();
    // Both of these hold somebody's documents - one as still-valid signed URLs,
    // the other as the file bytes themselves. Signing out has to drop them, or
    // the next person at this browser inherits them.
    forgetDocuments();
    paintSession();
    announce('Signed out.');
    navigate('#/login');
  });

  /*
   * The message the login screen should open with, set by the handler below and
   * consumed by renderLogin.
   *
   * It is a variable rather than a call to showError because of the ordering
   * that made the original version invisible. showError writes to the banner,
   * and the redirect that follows arrives as a hashchange - a task, not a
   * microtask - so render() ran afterwards and its clearError() wiped the
   * banner before anyone saw it. The only surviving copy was in the
   * screen-reader live region, which is never cleared: assistive tech was told
   * what happened and everybody else was dropped on a login page with no
   * explanation at all.
   *
   * Handing the message to the view that renders last is what makes it stick.
   */
  var loginNotice = null;

  /*
   * The API rejected a token - it expired, or the signing key was rotated
   * mid-session. store.js has already cleared the session by the time this
   * runs; all that is left is to say so and get out of a view the user can no
   * longer load.
   */
  auth.onExpired(function () {
    // Same reasoning as sign-out: the session is over, so the documents it
    // loaded go with it.
    forgetDocuments();
    paintSession();
    loginNotice = 'Your session expired. Sign in again.';
    announce(loginNotice);
    navigate('#/login');
  });

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

  /*
   * Bumped by renderLogin and by every render* function below that fetches
   * before it paints. Sign-out is the case that surfaced this: it clears the
   * session and swaps the hash to #/login synchronously, but a fetch already in
   * flight for the view being left - the employee list on first load, most
   * often - keeps running and, with nothing to stop it, painted its result over
   * the login form once it resolved. The address bar said #/login; the screen
   * did not, because nothing had told that fetch it was no longer wanted.
   *
   * Each render* function captures the id current when *it* started, and checks
   * it again before painting. A response that arrives after something newer has
   * started is for a screen nobody is looking at any more, and is dropped
   * instead of drawn.
   */
  var renderGeneration = 0;

  function failLoad(context, myGeneration) {
    return function (error) {
      if (myGeneration !== renderGeneration) return;
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

  // Escape dismisses whichever banner is showing, which until now was
  // reachable only by finding and clicking its button.
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (errorSlot.firstChild) clearError();
    if (noticeSlot.firstChild) clearNotice();
  });

  /*
   * The three document slots for whichever employee is on screen, or null.
   *
   * Module scope for the same reason commentEditor is: every repaint in this app
   * is driven by a write's response, and no write response carries documents. So
   * paintProfile and paintChecklist would blank the documents section on every
   * checkbox tick and every contact save if they had to be handed one. Loaded
   * once per render, passed through on every repaint after that.
   */
  var loadedDocuments = null;
  var loadedAttendance = null;

  /*
   * Documents by employee id, so list -> checklist -> back -> same checklist does
   * not refetch. Worth caching specifically: GET /documents costs three
   * sequential S3 HeadObject round trips server side (describe_slots in
   * common/documents.py), which made it the slowest call on the page.
   *
   * The TTL is not a nicety. Every downloadUrl in the payload is a presigned URL
   * signed for DOWNLOAD_TTL_SECONDS = 300, so a cache entry older than that
   * hands out links that answer 403. 240s leaves a minute of headroom.
   */
  var documentsCache = {};
  var DOCUMENTS_TTL_MS = 240 * 1000;

  function cachedDocuments(id) {
    var hit = documentsCache[id];
    if (!hit) return null;
    if (Date.now() - hit.fetchedAt > DOCUMENTS_TTL_MS) {
      delete documentsCache[id];
      return null;
    }
    return hit.documents;
  }

  function cacheDocuments(id, documents) {
    // null means "the call failed" - don't cache a failure as an answer.
    if (!documents) return;
    documentsCache[id] = { documents: documents, fetchedAt: Date.now() };
  }

  /*
   * Drop the cached document payloads. They carry presigned URLs that stay live
   * for up to five minutes, so the session ending - by sign-out or by expiry -
   * has to discard them rather than leave them for whoever is at this browser
   * next.
   */
  function forgetDocuments() {
    documentsCache = {};
    loadedDocuments = null;
    loadedAttendance = null;
  }

  // Mirrors ALLOWED_CONTENT_TYPES and MAX_UPLOAD_BYTES in common/documents.py.
  // Checked here only so an obviously-wrong file fails instantly instead of after
  // a round trip; the real limits are policy conditions on the presigned POST,
  // which is what stops a devtools user from stepping past these.
  var UPLOAD_TYPES = {
    'application/pdf': 'PDF',
    'image/jpeg': 'JPG',
    'image/png': 'PNG',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word'
  };
  var UPLOAD_MAX_BYTES = 10 * 1024 * 1024;

  /* ---------------------------------------------------------------- routing */

  function parseHash() {
    var raw = window.location.hash.replace(/^#\/?/, '');
    var segments = raw.split('/').filter(Boolean).map(decodeURIComponent);

    // All of these are matched before the fallthrough below, which sends
    // everything it does not recognise to the dashboard. A route added
    // after that line is a route that never matches.
    if (segments[0] === 'login') return { name: 'login' };
    if (segments[0] === 'me') return { name: 'profile' };
    if (segments[0] === 'dashboard') return { name: 'dashboard' };
    if (segments[0] === 'interns') return { name: 'interns' };
    if (segments[0] === 'tracking') return { name: 'tracking' };
    if (segments[0] === 'attendance') return { name: 'attendance' };

    if (segments[0] !== 'onboarding') return { name: 'dashboard' };
    if (segments.length === 1) return { name: 'list' };
    if (segments[1] === 'new') return { name: 'new' };
    if (segments.length === 3 && segments[2] === 'edit') return { name: 'edit', id: segments[1] };
    if (segments.length === 3 && segments[2] === 'checklist') return { name: 'checklist', id: segments[1] };
    return { name: 'dashboard' };
  }

  function navigate(hash) {
    // Assigning the hash it already has fires no hashchange, so render() would
    // never be called and the view would never paint. This comes up whenever
    // the guard redirects to where the browser already is - a signed-out reload
    // of #/login, most obviously.
    if (window.location.hash === hash) {
      render();
      return;
    }
    window.location.hash = hash;
  }

  /**
   * Sends a caller to the only view their role can use, or returns false to
   * mean "carry on".
   *
   * Four rules, in this order:
   *   no session          -> the login page, whatever they asked for
   *   session, on login   -> their home, so a reload does not re-ask
   *   employee, elsewhere -> their own profile
   *   official, on profile -> the HR dashboard
   *
   * Worth being clear about what this is: a way of keeping people out of
   * screens that would not work for them, not a security boundary. Nothing here
   * is trusted by the API.
   */
  function guard(route) {
    var session = auth.session();

    if (!session) {
      if (route.name === 'login') return false;
      navigate('#/login');
      return true;
    }

    if (route.name === 'login') {
      navigate(auth.home());
      return true;
    }

    if (auth.isEmployee() && route.name !== 'profile') {
      navigate('#/me');
      return true;
    }

    if (auth.isOfficial() && route.name === 'profile') {
      navigate('#/dashboard');
      return true;
    }

    return false;
  }

  function render() {
    var route = parseHash();

    // Before clearError, and before anything paints. A guard that redirects has
    // nothing to say and no view to leave behind - and clearing the banner
    // first would wipe the "your session expired" message on the way to the
    // login screen that message is explaining.
    if (guard(route)) return;

    // A banner belongs to the request that raised it, not to the next screen.
    clearError();

    // The live region is deliberately NOT cleared here. "Employee added." is
    // announced by the save, which then navigates - and clearing on arrival wiped
    // the message before a screen reader ever reached it. Stale text is harmless:
    // a live region speaks when its contents change, not because they are there.

    if (route.name === 'login') return renderLogin();
    if (route.name === 'profile') return renderProfile();
    if (route.name === 'dashboard') return renderDashboard();
    if (route.name === 'interns') return renderInterns();
    if (route.name === 'tracking') return renderTracking();
    if (route.name === 'attendance') return renderAttendanceSheet();
    if (route.name === 'list') return renderList();
    if (route.name === 'new') return renderForm(null);
    if (route.name === 'edit') return renderForm(route.id);
    if (route.name === 'checklist') return renderChecklist(route.id);
  }

  /* ------------------------------------------------------------ login view */

  function renderLogin() {
    // No fetch of its own, so nothing here ever checks this - but it still has
    // to move the counter, or signing out while a previous view's fetch is in
    // flight would leave that fetch looking current and free to paint over this
    // screen once it resolves. See renderGeneration above.
    renderGeneration++;

    setTitle('Sign in');

    // Consumed, not just read: an expiry notice belongs to the arrival that
    // caused it, and would otherwise reappear on the next visit to this screen.
    var notice = loginNotice;
    loginNotice = null;

    paint(ui.loginView(notice));
    focusHeading();
    wireLogin();
  }

  function wireLogin() {
    var form = document.getElementById('login-form');
    var submitting = false;

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      // The same guard the employee form uses: a second Enter while the first
      // request is in flight would send a second login.
      if (submitting) return;

      var username = form.elements.username.value.trim();
      var password = form.elements.password.value;

      if (!username || !password) {
        return failLogin('Enter your username and password.', username);
      }

      submitting = true;
      document.getElementById('login-submit').disabled = true;

      auth.signIn(username, password).then(function (session) {
        submitting = false;
        paintSession();
        announce('Signed in as ' + session.displayName + '.');
        navigate(auth.home());
      }, function (error) {
        submitting = false;
        // Repaints rather than patching the DOM, so the password field is
        // cleared - retyping it is the point of a failed login, and leaving a
        // wrong one in place invites a second identical attempt.
        failLogin(error.message, username);
      });
    });
  }

  function failLogin(message, username) {
    paint(ui.loginView(message));

    // Said out loud as well as shown. role="alert" on the message covers most
    // screen readers, but focus has just been thrown back to a repainted form
    // and announce() is the one channel that is not affected by that.
    announce(message);

    var form = document.getElementById('login-form');
    form.elements.username.value = username || '';
    // Focus the field they need to fix rather than the top of the page.
    (username ? form.elements.password : form.elements.username).focus();

    wireLogin();
  }

  /* ---------------------------------------------------------- profile view */

  /* --------------------------------------------------------- HR dashboard */

  /*
   * The official's landing page. No fetch - it is three links, not a report -
   * so this follows renderLogin's shape rather than renderList's: bump the
   * generation for the same reason renderLogin does (a stale fetch elsewhere
   * must not paint over whichever of these three screens is current), then
   * paint synchronously.
   */
  function renderDashboard() {
    renderGeneration++;
    setTitle('HR Dashboard');
    paint(ui.dashboardView());
    focusHeading();
  }

  var attendanceMonth = '';

  function currentKolkataMonth() {
    var parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit'
    }).formatToParts(new Date());
    var year = parts.filter(function (part) { return part.type === 'year'; })[0];
    var month = parts.filter(function (part) { return part.type === 'month'; })[0];
    return year.value + '-' + month.value;
  }

  function renderAttendanceSheet() {
    var myGeneration = ++renderGeneration;
    if (!attendanceMonth) attendanceMonth = currentKolkataMonth();
    showLoading();

    store.getAttendanceSheet(attendanceMonth).then(function (sheet) {
      if (myGeneration !== renderGeneration) return;
      setTitle('Attendance');
      paint(ui.attendanceSheetView(sheet));
      focusHeading();
      wireAttendanceSheet(sheet);
    }, failLoad('Could not load the attendance sheet.', myGeneration));
  }

  function wireAttendanceSheet(sheet) {
    var monthInput = document.getElementById('attendance-month');
    var searchInput = document.getElementById('attendance-search');
    var departmentInput = document.getElementById('attendance-department-filter');
    var roleInput = document.getElementById('attendance-role-filter');
    var downloadButton = document.getElementById('attendance-download');
    var table = document.querySelector('.attendance-table');

    function applyAttendanceFilters() {
      var term = (searchInput.value || '').trim().toLowerCase();
      var department = departmentInput.value;
      var role = roleInput.value;
      var visible = 0;

      Array.prototype.forEach.call(document.querySelectorAll('[data-attendance-row]'), function (row) {
        var show = (!term || row.getAttribute('data-search').indexOf(term) !== -1) &&
          (!department || row.getAttribute('data-department') === department) &&
          (!role || row.getAttribute('data-role') === role);
        row.hidden = !show;
        if (show) visible += 1;
      });
      document.getElementById('attendance-visible-count').textContent = visible;
    }

    monthInput.addEventListener('change', function () {
      if (!monthInput.value || monthInput.value === attendanceMonth) return;
      attendanceMonth = monthInput.value;
      renderAttendanceSheet();
    });
    searchInput.addEventListener('input', applyAttendanceFilters);
    departmentInput.addEventListener('change', applyAttendanceFilters);
    roleInput.addEventListener('change', applyAttendanceFilters);

    table.addEventListener('change', function (event) {
      var checkbox = event.target.closest('[data-action="edit-attendance"]');
      if (!checkbox) return;
      var previousValue = !checkbox.checked;
      clearError();
      checkbox.disabled = true;

      store.updateEmployeeAttendance(
        checkbox.getAttribute('data-employee-id'),
        checkbox.getAttribute('data-date'),
        { status: checkbox.checked ? 'present' : 'absent' }
      ).then(function () {
        notify('Attendance updated by HR.');
        renderAttendanceSheet();
      }, function (error) {
        checkbox.checked = previousValue;
        checkbox.disabled = false;
        showError(error);
      });
    });

    downloadButton.addEventListener('click', function () {
      downloadButton.disabled = true;
      clearError();
      store.downloadAttendanceCsv(sheet.month).then(function (download) {
        var url = window.URL.createObjectURL(download.blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = 'attendance-' + sheet.month + '.csv';
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
        downloadButton.disabled = false;
        notify('Attendance CSV downloaded.');
      }, function (error) {
        downloadButton.disabled = false;
        showError(error);
      });
    });
  }

  /*
   * The Interns dashboard - GET /staff/interns and GET /staff/employees
   * together, the second for the "Reassign manager" picker on each card.
   * Same shape as renderList: bump the generation, show a loading state,
   * fetch, repaint, wire the delegated click handler.
   */
  function renderInterns() {
    var myGeneration = ++renderGeneration;
    showLoading();

    Promise.all([store.listInterns(), store.listStaffEmployees()]).then(function (results) {
      if (myGeneration !== renderGeneration) return;

      var interns = results[0];
      var employees = results[1];

      setTitle('Interns');
      paint(ui.internsView(interns, employees));
      focusHeading();
      wireInternsActions(interns, employees);
    }, failLoad('Could not load interns.', myGeneration));
  }

  function wireInternsActions(interns, employees) {
    var list = document.getElementById('intern-cards');
    if (!list) return;

    list.addEventListener('click', function (event) {
      var card = event.target.closest('[data-id]');
      if (!card) return;
      var id = card.getAttribute('data-id');
      var intern = interns.filter(function (item) { return item.id === id; })[0];
      if (!intern) return;

      var unpromoteButton = event.target.closest('[data-action="unpromote"]');
      if (unpromoteButton) {
        return runUnpromote(unpromoteButton, intern);
      }

      var reassignButton = event.target.closest('[data-action="reassign"]');
      if (reassignButton) {
        var select = card.querySelector('[data-role="reassign-manager"]');
        var newManagerId = select ? select.value : '';
        if (!newManagerId) {
          showError({ message: 'Pick a manager before reassigning.' });
          return;
        }
        var manager = employees.filter(function (e) { return e.id === newManagerId; })[0];
        if (!window.confirm('Reassign ' + ui.fullName(intern) + ' to ' +
            (manager ? ui.fullName(manager) : newManagerId) + '?')) return;

        clearError();
        reassignButton.disabled = true;

        store.reassignManager(intern, newManagerId).then(function () {
          notify(ui.fullName(intern) + ' now reports to ' +
            (manager ? ui.fullName(manager) : newManagerId) + '.');
          renderInterns();
        }, function (error) {
          reassignButton.disabled = false;
          showError(sequenceError(error));
        });
      }
    });
  }

  /*
   * The Employee Tracking dashboard - onboarded, non-intern staff.
   */
  function renderTracking() {
    var myGeneration = ++renderGeneration;
    showLoading();

    store.listStaffEmployees().then(function (employees) {
      if (myGeneration !== renderGeneration) return;

      setTitle('Employee Tracking');
      paint(ui.trackingView(employees));
      focusHeading();
      wireTrackingActions(employees);
    }, failLoad('Could not load employees.', myGeneration));
  }

  function wireTrackingActions(employees) {
    var list = document.getElementById('staff-employee-cards');
    if (!list) return;

    list.addEventListener('click', function (event) {
      var button = event.target.closest('[data-action="unpromote"]');
      if (!button) return;
      var card = button.closest('[data-id]');
      var id = card.getAttribute('data-id');
      var employee = employees.filter(function (item) { return item.id === id; })[0];
      if (!employee) return;

      runUnpromote(button, employee);
    });
  }

  /*
   * Shared by both staff dashboards' "Undo move" button. Confirms, disables,
   * runs the un-promote sequence, and repaints whichever dashboard the
   * caller is currently on.
   */
  function runUnpromote(button, staffRecord) {
    var isIntern = staffRecord.employmentType === 'Intern';

    if (!window.confirm('Move ' + ui.fullName(staffRecord) + ' back to the onboarding ' +
        'dashboard? Their checklist and every HR note on it come back exactly as they were.')) {
      return;
    }

    clearError();
    button.disabled = true;

    store.unpromote(staffRecord).then(function () {
      notify(ui.fullName(staffRecord) + ' is back on the onboarding dashboard.');
      if (isIntern) renderInterns(); else renderTracking();
    }, function (error) {
      button.disabled = false;
      showError(sequenceError(error));
    });
  }

  /*
   * A rejection from one of store.promote/unpromote/reassignManager carries
   * `error.step` naming which call in the sequence failed. Every step is
   * idempotent and the destructive one is always last, so the honest thing
   * to tell HR is which part is done and that trying again is safe - not
   * just "something went wrong".
   */
  function sequenceError(error) {
    if (!error.step) return error;
    error.message = error.message + ' (failed at the "' + error.step + '" step - ' +
      'nothing was lost; trying again will pick up from where it stopped.)';
    return error;
  }

  /*
   * The employee's own record. The whole employee side of the app.
   *
   * No id argument, and there cannot be one: store.getOwnProfile reads it out of
   * the token, so this cannot be pointed at somebody else by changing a URL. The
   * API would refuse it anyway - require_self in common/handler.py - but the
   * frontend having no way to *ask* is what keeps the two honest.
   */
  function renderProfile() {
    var myGeneration = ++renderGeneration;
    showLoading();

    // Both at once. The documents call is allowed to fail without taking the page
    // with it - a profile that renders with an apologetic documents section beats
    // a blank screen - so it is caught here rather than in the Promise.all.
    Promise.all([
      store.getOwnProfile(),
      // [] and not null: documentsSection reads null as "still loading" now, so
      // a failure here has to be an empty answer rather than an absent one.
      store.getOwnDocuments().catch(function () { return []; }),
      store.getOwnAttendance().catch(function () { return null; })
    ]).then(function (results) {
      // Still the current screen - see renderGeneration.
      if (myGeneration !== renderGeneration) return;

      var employee = results[0];
      loadedDocuments = results[1];
      loadedAttendance = results[2];
      // null, not an error. POST /login does not check that an employee number
      // exists - it has no table access, deliberately - so a mistyped number
      // reaches this screen with a perfectly valid session. So does a session
      // opened before this feature existed, whose token names no employee at all.
      if (!employee) {
        setTitle('No record found');
        paint(ui.profileMissingView(auth.employeeId()));
        focusHeading();
        return;
      }

      paintProfile(employee);
    }, failLoad('Could not load your profile.', myGeneration));
  }

  /*
   * Paint, then re-wire. Called on load and again after a successful save,
   * because the response to the save *is* the new record - the same repaint-from-
   * the-server rule the checklist view follows.
   */
  function paintProfile(employee) {
    // The login could only tell us our own employee number; this is the first
    // moment a real name exists, so the header chip gets it now.
    auth.setDisplayName(ui.fullName(employee));
    paintSession();

    setTitle('My profile');
    paint(ui.profileView(employee, loadedDocuments, loadedAttendance));
    focusHeading();

    // Wired before the contact form, because an archived record has drop zones
    // that are absent and a contact form that is absent too - neither wiring
    // depends on the other.
    wireDropZones(employee);
    wireOwnAttendance();

    var form = document.getElementById('contact-form');
    // Absent on an archived record - it is read-only server side, so there is no
    // form to wire and no button to press.
    if (!form) return;

    var submitting = false;

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      if (submitting) return;

      var values = readForm(form);
      var errors = validateContact(values);
      showErrors(form, errors);
      if (Object.keys(errors).length) return;

      var submitButton = form.querySelector('[type="submit"]');
      var submitLabel = submitButton.textContent;

      submitting = true;
      submitButton.disabled = true;
      submitButton.textContent = 'Saving…';
      clearError();

      store.updateOwnContact(values).then(function (saved) {
        notify('Your details were saved.');
        // Repainting discards this form and its listener along with it, so the
        // submitting flag does not need resetting on the way out.
        paintProfile(saved);
      }, function (error) {
        submitting = false;
        submitButton.disabled = false;
        submitButton.textContent = submitLabel;
        showSaveError(form, error);
      });
    });
  }

  function wireOwnAttendance() {
    var form = document.getElementById('attendance-form');
    if (!form) return;
    var checkbox = form.elements.present;
    var submitting = false;

    checkbox.addEventListener('change', function () {
      if (submitting) return;
      var previousValue = !checkbox.checked;
      submitting = true;
      checkbox.disabled = true;
      clearError();

      store.markOwnAttendance({
        status: checkbox.checked ? 'present' : 'absent'
      }).then(function () {
        notify('Today\'s attendance was saved.');
        renderProfile();
      }, function (error) {
        submitting = false;
        checkbox.checked = previousValue;
        checkbox.disabled = false;
        showError(error);
      });
    });
  }

  /* ------------------------------------------------------------- list view */

  function applyFilters(employees) {
    var term = filters.search.trim().toLowerCase();

    return employees.filter(function (employee) {
      if (filters.department && employee.department !== filters.department) return false;
      if (filters.status && employee.status !== filters.status) return false;
      if (!term) return true;

      // The id is searchable now that it is something a person knows by heart -
      // "E1024" is exactly what someone would paste in from a payroll export,
      // and it used to be a UUID nobody could have typed.
      //
      // email used to need coercing here, because the employee role was served a
      // response with no email on it and the haystack read "... undefined". This
      // list is officials-only now and email is a required field, so it is always
      // a string - the || '' stays as a cheap guard, not as a load-bearing one.
      var haystack = (employee.id + ' ' + ui.fullName(employee) + ' ' +
        (employee.email || '')).toLowerCase();
      return haystack.indexOf(term) !== -1;
    });
  }

  function refreshRows() {
    var list = document.getElementById('employee-cards');
    if (!list) return;

    var visible = applyFilters(loadedEmployees);
    // One renderer. There used to be two, because the employee role had a
    // read-only list of everybody; that role has its own screen now and never
    // reaches this one.
    list.innerHTML = ui.employeeCards(visible);
    document.getElementById('record-count').textContent =
      ui.countLabel(visible.length, loadedEmployees.length);
  }

  function renderList() {
    var myGeneration = ++renderGeneration;
    showLoading();

    store.listEmployees().then(function (employees) {
      if (myGeneration !== renderGeneration) return;

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

      document.getElementById('employee-cards').addEventListener('click', function (event) {
        var button = event.target.closest('[data-action="delete"]');
        if (!button) return;

        // The card carries data-id, so this reads the attribute rather than
        // the element type - the row used to be a <tr> and is now an <li>.
        var card = button.closest('[data-id]');
        var id = card.getAttribute('data-id');
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
          notify(ui.fullName(employee) + ' was removed from the list and marked ' +
            archived.archivedAs + '.');
          renderList();
        }, function (error) {
          // The list is still on screen and still correct apart from this row,
          // so leave it be and just say what happened.
          button.disabled = false;
          showError(error);
        });
      });
    }, failLoad('Could not load employees.', myGeneration));
  }

  /* ------------------------------------------------------------- form view */

  var REQUIRED_FIELDS = [
    { name: 'employeeId', label: 'Employee ID' },
    { name: 'firstName', label: 'First name' },
    { name: 'lastName', label: 'Last name' },
    { name: 'email', label: 'Work email' },
    { name: 'department', label: 'Department' },
    { name: 'jobTitle', label: 'Job title' },
    { name: 'startDate', label: 'Start date' },
    { name: 'employmentType', label: 'Employment type' }
  ];

  var EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  /*
   * Mirrors EMPLOYEE_ID_PATTERN in common/models.py, with one deliberate
   * difference: this accepts lower case where the server's does not. The server
   * upper-cases before it matches, so "e1024" is a perfectly valid thing to
   * type - rejecting it here would refuse input the API would have accepted.
   */
  var EMPLOYEE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9-]{1,19}$/;

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

    if (values.employeeId && !EMPLOYEE_ID_PATTERN.test(values.employeeId)) {
      errors.employeeId =
        'Employee ID must be 2-20 characters, using letters, digits and hyphens only.';
    }

    return errors;
  }

  /**
   * The employee's own three fields. Mirrors validate_self_fields in
   * common/models.py.
   *
   * Separate from validate() above, which demands the seven facts HR asserts.
   * All three of these are optional - somebody who has not filled in their
   * address yet is a new starter, not an invalid record - so this only ever
   * checks the shape of what was actually typed.
   */
  function validateContact(values) {
    var errors = {};

    if (values.personalEmail && !EMAIL_PATTERN.test(values.personalEmail)) {
      errors.personalEmail = 'Enter a valid email address.';
    }

    // Mirrors ADDRESS_MAX_LENGTH. The textarea carries the same number as a
    // maxlength, so this only fires for a value that got in another way.
    if (values.address && values.address.length > 300) {
      errors.address = 'Keep the address under 300 characters.';
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
    var myGeneration = ++renderGeneration;

    // Both forms wait now, the add form included: its dropdowns are built from
    // the employees the API returns, so there is nothing to render until that
    // list is in.
    showLoading();

    var load = Promise.all([
      id ? store.getEmployee(id) : null,
      ensureEmployees()
    ]).then(function (results) { return results[0]; });

    load.then(function (employee) {
      if (myGeneration !== renderGeneration) return;

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
          notify(employee ? 'Changes saved.' : 'Employee added.');
          navigate('#/onboarding');   // nothing to restore; navigating discards this DOM
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
            notify(ui.fullName(employee) + ' was removed from the list and marked ' +
              archived.archivedAs + '.');
            navigate('#/onboarding');
          }, function (error) {
            // The form is still filled in and still valid; keep it usable.
            deleteButton.disabled = false;
            showError(error);
          });
        });
      }
    }, failLoad(id ? 'Could not load this employee.' : 'Could not load the form.', myGeneration));
  }

  /* ------------------------------------------------------------- documents */

  /*
   * Drag-and-drop and click-to-browse for the three slots.
   *
   * No wiring at all when the section is read-only - HR's page and an archived
   * employee's both render slots with no `data-slot` and no file input, so the
   * loop below finds nothing and does nothing.
   */
  function wireDropZones(employee) {
    var section = document.getElementById('documents');
    if (!section) return;

    var zones = section.querySelectorAll('.doc-slot[data-slot]');
    if (!zones.length) return;

    Array.prototype.forEach.call(zones, function (zone) {
      var slot = zone.getAttribute('data-slot');
      var input = zone.querySelector('input[type="file"]');

      // dragenter AND dragover, both preventDefault. Without preventDefault on
      // dragover the browser refuses the drop and then navigates to the file,
      // replacing the whole app with a PDF viewer - the classic version of this
      // bug, and the reason for the document-level guard further down too.
      ['dragenter', 'dragover'].forEach(function (name) {
        zone.addEventListener(name, function (event) {
          event.preventDefault();
          zone.classList.add('is-dragover');
        });
      });

      ['dragleave', 'dragend'].forEach(function (name) {
        zone.addEventListener(name, function () {
          zone.classList.remove('is-dragover');
        });
      });

      zone.addEventListener('drop', function (event) {
        event.preventDefault();
        zone.classList.remove('is-dragover');
        var files = event.dataTransfer && event.dataTransfer.files;
        if (files && files.length) uploadFile(employee, slot, files[0]);
      });

      if (input) {
        input.addEventListener('change', function () {
          if (input.files && input.files.length) {
            uploadFile(employee, slot, input.files[0]);
          }
        });
      }
    });
  }

  function slotLabel(slot) {
    var match = (loadedDocuments || []).filter(function (entry) {
      return entry.slot === slot;
    })[0];
    return match ? match.label : 'Document';
  }

  function slotStatus(slot, text) {
    var node = document.querySelector('[data-status-for="' + slot + '"]');
    if (node) node.textContent = text || '';
  }

  /*
   * One file, one slot: check it, get a ticket, send it to S3, redraw.
   *
   * The redraw is the documents section ALONE, and that is deliberate. Everywhere
   * else a write repaints the whole view from its response, because the response
   * is the new record. Here a full paintProfile would throw away whatever the
   * employee had typed into the contact form and not yet saved - so this replaces
   * one element and re-wires it.
   */
  function uploadFile(employee, slot, file) {
    var problem = uploadProblem(file);
    if (problem) {
      slotStatus(slot, problem);
      announce(problem);
      return;
    }

    clearError();
    slotStatus(slot, 'Uploading ' + file.name + '\u2026');
    var zone = document.querySelector('.doc-slot[data-slot="' + slot + '"]');
    if (zone) zone.classList.add('is-uploading');

    store.requestUpload(employee.id, slot, file).then(function (ticket) {
      return store.uploadToS3(ticket, file);
    }).then(function () {
      return store.getDocuments(employee.id);
    }).then(function (documents) {
      loadedDocuments = documents;
      repaintDocuments(employee);

      // Named, not just "Uploaded" - three zones look alike, and the banner is
      // shared by the whole page.
      var label = slotLabel(slot);
      notify(label + ' uploaded.');
      slotStatus(slot, 'Uploaded.');
    }, function (error) {
      if (zone) zone.classList.remove('is-uploading');
      slotStatus(slot, '');
      // A field-level 400 belongs beside the slot; anything else is a banner.
      var fields = error.fields || {};
      var message = fields.filename || fields.contentType || error.message;
      slotStatus(slot, message);
      announce(message);
    });
  }

  /* The message to show instead of uploading, or '' if the file is fine. */
  function uploadProblem(file) {
    if (!file) return 'No file was chosen.';
    if (!UPLOAD_TYPES[file.type]) {
      return 'Upload a PDF, JPG, PNG or Word document.';
    }
    if (file.size > UPLOAD_MAX_BYTES) {
      return 'That file is larger than 10 MB.';
    }
    if (file.size === 0) return 'That file is empty.';
    return '';
  }

  /*
   * Swaps the #documents section for a freshly rendered one.
   *
   * The low-level half, shared by the employee's upload path and the officials'
   * deferred documents load - the two disagree on `editable`, which is why it is
   * an argument.
   */
  function repaintDocumentsSection(documents, editable) {
    var section = document.getElementById('documents');
    if (!section) return null;

    var wrapper = document.createElement('div');
    wrapper.innerHTML = ui.documentsSection(documents, editable);
    var fresh = wrapper.firstChild;

    section.parentNode.replaceChild(fresh, section);
    return fresh;
  }

  /* Replaces the documents section in place and re-wires it. See uploadFile. */
  function repaintDocuments(employee) {
    var editable = !employee.archived && auth.isEmployee();
    if (repaintDocumentsSection(loadedDocuments, editable)) {
      wireDropZones(employee);
    }
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

  /*
   * The officials' view of one employee.
   *
   * This used to be one Promise.all over getEmployee and getDocuments behind a
   * "Loading..." placeholder, which made the page feel far slower than the list
   * it was opened from. Two things were wrong with that and both are fixed here:
   *
   *   1. It refetched an employee it already had. The list Scan returns whole
   *      records, checklist included (see handlers/list_employees.py), so
   *      arriving from the list there is nothing to wait for - paint the cached
   *      record at once and revalidate behind it.
   *   2. Promise.all made the checklist wait on the documents call, which is the
   *      slower of the two by a distance - three serial S3 HeadObjects - even
   *      though documents render at the *bottom* of the page. The two are
   *      independent now, so the checklist paints without them.
   *
   * What is deliberately NOT done: caching the employee past this paint. The
   * revalidation always runs, because a checklist another official ticked a
   * minute ago must not stay stale on screen.
   */
  function renderChecklist(id) {
    var myGeneration = ++renderGeneration;
    commentEditor = null;

    var cached = loadedEmployees.filter(function (item) {
      return item.id === id;
    })[0];

    // Documents come from their own cache and their own request; the checklist
    // never waits on either.
    loadedDocuments = cachedDocuments(id);

    if (cached && !cached.archived) {
      setTitle(ui.fullName(cached) + ' \u2014 checklist');
      paintChecklist(cached);
    } else {
      // No cache: a deep link or a hard refresh. A skeleton rather than a bare
      // line, because this is the one path that genuinely waits.
      root.setAttribute('aria-busy', 'true');
      root.innerHTML = ui.checklistSkeleton();
    }

    loadDocuments(id, myGeneration);

    store.getEmployee(id).then(function (employee) {
      if (myGeneration !== renderGeneration) return;

      if (!employee) {
        setTitle('Not found');
        paint(ui.notFoundView());
        focusHeading();
        return;
      }

      setTitle(ui.fullName(employee) + ' \u2014 checklist');
      // Repaints even when a cached copy is already on screen: this is the
      // authoritative record, and the point of revalidating is to replace it.
      // paintChecklist re-wires from scratch, so there is nothing to clean up.
      paintChecklist(employee);
    }, function (error) {
      if (myGeneration !== renderGeneration) return;

      // A cached record is already readable on screen, so a failed
      // revalidation is a banner rather than a replacement - tearing down a
      // usable view to say "could not load" would be strictly worse.
      if (cached && !cached.archived) {
        showError(error);
        return;
      }
      failLoad('Could not load this checklist.', myGeneration)(error);
    });
  }

  /*
   * Documents for one employee, painted into whatever view is already on screen.
   *
   * Separate from the employee request so neither blocks the other, and
   * generation-guarded like every other fetch here: a slow documents response
   * for a screen the user has left must not paint.
   */
  function loadDocuments(id, myGeneration) {
    if (loadedDocuments) return;   // already cached - nothing to fetch

    store.getDocuments(id).then(function (documents) {
      if (myGeneration !== renderGeneration) return;

      cacheDocuments(id, documents);
      loadedDocuments = documents;

      // Only replaces the documents section, so a checklist the user has
      // already started ticking is left alone.
      var section = document.getElementById('documents');
      if (section) repaintDocumentsSection(documents, false, auth.isOfficial());
    }, function () {
      if (myGeneration !== renderGeneration) return;

      // An empty array, not null: null is the "still loading" state, so leaving
      // it would make the next tick's repaint show the loading line forever.
      loadedDocuments = [];

      // A documents failure must not take the page with it - the checklist is
      // the point of this screen. Say so in place of the slots.
      var section = document.getElementById('documents');
      if (section) {
        var grid = section.querySelector('.doc-grid');
        if (grid) {
          grid.innerHTML = '<p class="doc-empty">Documents could not be loaded.</p>';
        }
      }
    });
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
  /**
   * True only when the checklist screen needs a reporting-manager picker to
   * render its promote control: an intern, finished, not archived. Every
   * other case (not finished, not an intern, already archived) has enough
   * information in `employee` alone.
   */
  function needsManagerPicker(employee) {
    return !employee.archived && employee.employmentType === 'Intern' &&
      employee.status === 'Onboarded';
  }

  function paintChecklist(employee, focus, managers) {
    paint(ui.checklistView(employee, commentEditor, loadedDocuments, managers));

    /*
     * A tick replaces the whole view, which throws away the control the user is
     * standing on - so a keyboard user is dumped back to the top of the document
     * on every single tick, and there is no way to work down the list. Put focus
     * back where it was; only a fresh arrival gets the heading.
     */
    restoreFocus(focus);
    wireComments(employee);
    wirePromoteSection(employee);

    // The manager picker needs a second fetch, and most checklist views never
    // need it - not finished yet, or not an intern. Only kick it off when the
    // caller has not already supplied `managers` (paintChecklist's own
    // recursive call below does supply it, so this does not loop) and the
    // screen just painted actually has a picker to fill in.
    if (managers === undefined && needsManagerPicker(employee)) {
      var myGeneration = renderGeneration;
      store.listStaffEmployees().then(function (loadedManagers) {
        if (myGeneration !== renderGeneration) return;
        paintChecklist(employee, null, loadedManagers);
      }, function () {
        if (myGeneration !== renderGeneration) return;
        paintChecklist(employee, null, []);
      });
    }

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

  /*
   * The "Move to main employee dashboard" / "Move to intern dashboard"
   * button on the checklist screen. Absent from the DOM whenever
   * promoteSection() decided there is nothing to click - not finished,
   * archived, or (for an intern) no manager to pick from yet - so this is a
   * no-op in every one of those cases.
   */
  function wirePromoteSection(employee) {
    var section = document.getElementById('promote-section');
    if (!section) return;

    section.addEventListener('click', function (event) {
      var isIntern = !!event.target.closest('[data-action="promote-intern"]');
      var isEmployee = !!event.target.closest('[data-action="promote-employee"]');
      if (!isIntern && !isEmployee) return;

      var reportingManagerId = null;
      if (isIntern) {
        var select = document.getElementById('reporting-manager');
        reportingManagerId = select ? select.value : '';
        if (!reportingManagerId) {
          showError({ message: 'Pick a reporting manager before moving this intern.' });
          return;
        }
      }

      var destination = isIntern ? 'the intern dashboard' : 'the main employee dashboard';
      if (!window.confirm('Move ' + ui.fullName(employee) + ' to ' + destination +
          '? Their onboarding checklist moves with them as history.')) {
        return;
      }

      var button = event.target.closest('button');
      clearError();
      button.disabled = true;

      store.promote(employee, reportingManagerId).then(function () {
        notify(ui.fullName(employee) + ' was moved to ' + destination + '.');
        navigate('#/onboarding');
      }, function (error) {
        button.disabled = false;
        showError(sequenceError(error));
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
      notify(text
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

  /*
   * A file dropped anywhere that is not a drop zone. Without these two the
   * browser navigates away from the app and renders the file instead, which looks
   * exactly like a crash - and it is easy to miss a zone by a few pixels.
   */
  ['dragover', 'drop'].forEach(function (name) {
    document.addEventListener(name, function (event) {
      if (event.target.closest && event.target.closest('.doc-slot[data-slot]')) return;
      event.preventDefault();
    });
  });

  window.addEventListener('hashchange', render);

  paintSession();

  if (!window.location.hash) {
    // Not '#/dashboard' any more. A signed-in official ends up there anyway,
    // via the guard; a signed-out visitor would have landed on a view that
    // immediately redirected, painting the list heading on the way past.
    window.location.hash = auth.session() ? auth.home() : '#/login';
  } else {
    render();
  }
})(window.App);
