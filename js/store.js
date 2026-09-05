/**
 * Data access layer - the seam between the UI and where employees actually live.
 *
 * Two functions here touch fetch(), not one. `request()` handles every call to
 * our own API; `uploadToS3()` is the exception, and it exists because a presigned
 * S3 URL cannot go through request() at all - see the comment on it. Nothing else
 * may grow a third.
 *
 * That is now DynamoDB, reached through API Gateway. Every function here is a
 * fetch(); there is no local copy of anything. If the API is unreachable the UI
 * shows an error, because there is nothing else it could honestly show.
 *
 * The contract did not change when the bodies did - the callers in app.js were
 * already awaiting promises and already got copies rather than live references.
 * What changed is that calls are now slow and can fail, which is what the
 * rejection handling in app.js is for.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  /**
   * The Error every rejected request carries. Callers branch on `status`, never
   * on the message text:
   *
   *   message  string  safe to show a human
   *   status   number  HTTP status, or 0 when we never reached the server
   *   code     string  'ValidationError' | 'NotFound' | ... | null
   *   fields   object  { email: 'Enter a valid email address.' } | null
   *   cause    Error   the underlying TypeError, only when status is 0
   */
  function apiError(status, payload, cause) {
    var detail = (payload && payload.error) || {};
    var message = detail.message ||
      (status === 0
        ? 'Could not reach the server. Check your connection and try again.'
        : 'The server returned an unexpected error (' + status + ').');

    var error = new Error(message);
    error.status = status;
    error.code = detail.code || null;
    error.fields = detail.fields || null;
    if (cause) error.cause = cause;
    return error;
  }

  /**
   * Every call to our own API goes through here. (Uploads to S3 do not, and
   * cannot - uploadToS3 below says why.)
   *
   * Two things fetch gets wrong for our purposes, both handled here: it resolves
   * happily on a 500, and response.json() rejects on an empty body - which is
   * exactly what a 204 from DELETE is.
   */
  function request(method, path, body, anonymous, responseType) {
    var options = { method: method, headers: {} };

    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    // Every route except POST /login is behind the authorizer, so the token goes
    // on here rather than at each call site - this is the only fetch() in the
    // app, which is what makes that a single line instead of eight.
    if (!anonymous) {
      var token = App.auth.token();
      if (token) options.headers['Authorization'] = 'Bearer ' + token;
    }

    return fetch(App.API_BASE_URL + path, options).then(function (response) {
      if (response.status === 204) return null;

      if (response.ok && responseType === 'blob') {
        return response.blob().then(function (blob) {
          return {
            blob: blob,
            disposition: response.headers.get('Content-Disposition') || ''
          };
        });
      }

      return response.text().then(function (text) {
        var payload = null;

        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (parseError) {
            payload = null; // a 502 from API Gateway is HTML; the status still tells the story
          }
        }

        if (!response.ok) {
          // 401 is the one status that is about the session rather than about
          // the request. It means the token expired, or the stack was
          // redeployed with a new signing key while someone was logged in -
          // either way retrying will not help and every other call is about to
          // fail the same way. Handled once, here, rather than at seven call
          // sites: clear the session and let app.js say so.
          //
          // 403 deliberately falls through to the caller. That is an employee
          // touching a write endpoint - their session is fine, and signing them
          // out for it would be a bug, not a safeguard.
          if (response.status === 401 && !anonymous) App.auth.expire();
          throw apiError(response.status, payload);
        }
        return payload;
      });
    }, function (networkError) {
      // Second argument to .then, not a trailing .catch. A .catch here would also
      // catch the apiError thrown just above and wrap it a second time.
      throw apiError(0, null, networkError);
    });
  }

  /**
   * Only the fields a form is allowed to *change*. The API drops unknown keys
   * anyway, but sending exactly nine makes the request body self-documenting in
   * devtools and stops a control added to the form later from leaking into every
   * write.
   *
   * `employeeId` is not here on purpose - it is set at creation and never after.
   * See pickCreatable.
   */
  var EDITABLE_FIELDS = [
    'firstName', 'lastName', 'email', 'phone',
    'department', 'jobTitle', 'manager', 'startDate', 'employmentType'
  ];

  /**
   * The three an employee may change on their own record. Mirrors
   * SELF_EDITABLE_FIELDS in common/models.py.
   *
   * Read this beside EDITABLE_FIELDS above, because the overlap is the
   * interesting part: `phone` is on both lists and therefore has two writers,
   * while `personalEmail` and `address` are on this one only - the officials form
   * has no control for them and the API's PUT whitelist cannot name them.
   */
  var SELF_FIELDS = ['phone', 'personalEmail', 'address'];

  function pick(fields, input) {
    var out = {};
    fields.forEach(function (field) {
      out[field] = typeof input[field] === 'string' ? input[field].trim() : (input[field] || '');
    });
    return out;
  }

  function pickEditable(input) {
    return pick(EDITABLE_FIELDS, input);
  }

  /**
   * Create sends one field more than update does: the employee number, which
   * becomes the record's id and its DynamoDB partition key.
   *
   * The asymmetry is the point rather than an oversight. The id cannot be
   * changed after the record exists - DynamoDB has no way to move an item to a
   * different partition key - so it is settable exactly once, and keeping it out
   * of the update body means a stray `employeeId` on the edit form can never
   * even look like it might rename someone.
   */
  function pickCreatable(input) {
    return pick(EDITABLE_FIELDS.concat('employeeId'), input);
  }

  function pickSelf(input) {
    return pick(SELF_FIELDS, input);
  }

  function employeePath(id) {
    return '/employees/' + encodeURIComponent(id);
  }

  function checklistPatch(employeeId, itemId, body) {
    return request(
      'PATCH',
      employeePath(employeeId) + '/checklist/' + encodeURIComponent(itemId),
      body
    );
  }

  App.store = {
    /**
     * Credentials in, signed token out. The only anonymous call in the app -
     * it is where a token comes from, so it cannot carry one.
     *
     * Rejects with the usual apiError: 401 for bad credentials (message already
     * worded for a human by src/handlers/login.py), 400 for a missing field.
     * Note the `true` - without it the 401 handler above would fire on every
     * failed login attempt and call expire() on a session that never existed.
     */
    login: function (username, password) {
      return request('POST', '/login', {
        username: username,
        password: password
      }, true);
    },

    listEmployees: function () {
      // The { employees, count } envelope is an API detail; callers want the array.
      return request('GET', '/employees').then(function (payload) {
        return payload.employees;
      });
    },

    /**
     * The one function that treats 404 as data rather than as a failure. A stale
     * bookmark to a deleted employee is a normal thing for someone to have, and
     * the router renders notFoundView() for it. Every other function here rejects
     * on 404 - you can only reach those from a row we just listed, so if it is
     * gone then something is genuinely wrong and the user should hear about it.
     */
    getEmployee: function (id) {
      return request('GET', employeePath(id)).catch(function (error) {
        if (error.status === 404) return null;
        throw error;
      });
    },

    /**
     * Rejects with a 409 when that employee number is already taken. The error
     * carries `fields.employeeId`, so app.js paints it under the input like any
     * validation message - see showSaveError.
     */
    createEmployee: function (input) {
      // The caller supplies the id; the API embeds all eight checklist entries on
      // the new record, so the response is already complete.
      return request('POST', '/employees', pickCreatable(input));
    },

    updateEmployee: function (id, input) {
      // A full replace of the nine editable fields. The checklist is an attribute
      // of the same item, and the server's whitelist never names it - so progress
      // survives an edit. Note pickEditable, not pickCreatable: the employee
      // number is not among those nine and cannot be changed here.
      return request('PUT', employeePath(id), pickEditable(input));
    },

    /*
     * Takes someone off the employee list. Still a DELETE, and deliberately so -
     * from here that is exactly what it is - but the server archives rather than
     * erases: the record and its embedded checklist stay in DynamoDB stamped
     * with a terminal state, and stop accepting writes. src/handlers/delete_employee.py
     * has the reasoning.
     *
     * Resolves the archived employee. That is the whole reason it is a 200 and
     * not the old 204: the caller needs to tell the user which state it landed
     * in, and asking the caller to work that out from the checklist would put a
     * second copy of the rule in the frontend.
     */
    archiveEmployee: function (id) {
      return request('DELETE', employeePath(id));
    },

    /**
     * The signed-in employee's own record, whole - or null if there is no such
     * record.
     *
     * The id comes from the token rather than from a caller, which is what makes
     * this "own": app.js cannot ask for somebody else's profile by passing a
     * different argument, because there is no argument.
     *
     * null on 404 for the same reason getEmployee does it, and it is not a
     * hypothetical here. POST /login does not check that an employee number
     * exists - it has no table access, by design - so a typo'd number gets a
     * perfectly good session and discovers the problem right here. The router
     * paints profileMissingView for it.
     */
    getOwnProfile: function () {
      var id = App.auth.employeeId();
      if (!id) return Promise.reject(apiError(0, null));

      return request('GET', employeePath(id)).catch(function (error) {
        if (error.status === 404) return null;
        throw error;
      });
    },

    /**
     * The three fields an employee fills in themselves.
     *
     * PATCH, and a different route from updateEmployee's PUT: this sends only the
     * keys the form holds, and the server leaves everything it does not name
     * alone. Resolves the employee's own view of the whole record, already
     * trimmed the same way getOwnProfile's response is, so the caller can repaint
     * from it without a follow-up read.
     */
    updateOwnContact: function (values) {
      var id = App.auth.employeeId();
      if (!id) return Promise.reject(apiError(0, null));

      return request('PATCH', employeePath(id) + '/contact', pickSelf(values));
    },

    /**
     * The three document slots for one employee, uploaded or not.
     *
     * Always three, and an empty one carries no `downloadUrl` - so the caller
     * renders an empty drop zone from `uploaded: false` rather than from a gap in
     * the array. See src/common/documents.py.
     */
    getDocuments: function (id) {
      return request('GET', employeePath(id) + '/documents').then(function (payload) {
        return payload.documents;
      });
    },

    /** The signed-in employee's own documents. Mirrors getOwnProfile. */
    getOwnDocuments: function () {
      var id = App.auth.employeeId();
      if (!id) return Promise.reject(apiError(0, null));
      return App.store.getDocuments(id);
    },

    /**
     * Ask the API for permission to upload one file, and get back a ticket.
     *
     * The file is not sent here - this is a small JSON call that returns a
     * presigned POST for uploadToS3 to send the bytes to. Rejects 403 for
     * somebody else's slot, 404 for an employee who does not exist, 409 for an
     * archived one and 400 for a file the server will not take.
     */
    requestUpload: function (employeeId, slot, file) {
      return request('POST',
        employeePath(employeeId) + '/documents/' + encodeURIComponent(slot),
        { filename: file.name, contentType: file.type });
    },

    /**
     * The file itself, straight to S3. The one fetch() in this file that is not
     * request().
     *
     * It cannot be request(), for three independent reasons: request() prefixes
     * every path with App.API_BASE_URL and has no absolute-URL escape hatch; it
     * JSON-stringifies the body, which would destroy a File; and it attaches
     * `Authorization`, which a presigned URL rejects outright because the
     * signature covers an exact set of headers.
     *
     * Three things about the FormData are load-bearing, and each is a 403 from S3
     * if you get it wrong:
     *
     *   - Every field the ticket carries goes in, verbatim and unmodified. They
     *     are signed; editing one invalidates the policy.
     *   - The file goes in LAST. S3 ignores every field that appears after the
     *     file part, so a file-first body arrives looking like it has no policy.
     *   - Nothing else goes in at all. An extra field - an `acl`, a
     *     `success_action_status` - is "Invalid according to Policy: Extra input
     *     fields".
     *
     * No Content-Type header is set by hand either: the browser has to set it, so
     * that it can put the multipart boundary in it.
     *
     * Success is a 204 with an empty body, so this checks `ok` and never parses.
     */
    uploadToS3: function (ticket, file) {
      var form = new FormData();

      Object.keys(ticket.fields).forEach(function (name) {
        form.append(name, ticket.fields[name]);
      });
      form.append('file', file);

      return fetch(ticket.url, { method: 'POST', body: form }).then(function (response) {
        if (response.ok) return null;

        // S3 answers with XML, not our error envelope, so there is no message
        // worth showing. 403 is overwhelmingly one of two things: the five-minute
        // ticket expired while the user was choosing a file, or the file broke a
        // policy condition - and both are fixed by trying again.
        throw apiError(response.status, {
          error: {
            code: 'UploadFailed',
            message: response.status === 403
              ? 'That upload window expired. Try the file again.'
              : 'The upload was refused. Check the file and try again.'
          }
        });
      }, function (networkError) {
        // Second argument to .then, not a trailing .catch, so the apiError above
        // is not wrapped twice - same reasoning as request().
        //
        // Note the most likely cause here is not the network: it is the bucket
        // missing its CORS rule, which a browser reports as an opaque TypeError
        // with no status at all.
        throw apiError(0, null, networkError);
      });
    },

    /**
     * The employee/intern promote/restore/reassign primitives, each its own
     * fetch to its own endpoint - see docs/database-design.md#promotion for
     * why these are separate calls rather than one atomic request. `promote`,
     * `unpromote` and `reassignManager` below sequence them; nothing else
     * should call these eleven functions directly.
     */
    promoteToEmployee: function (employeeId) {
      return request('POST', '/staff/employees', { employeeId: employeeId });
    },

    promoteToIntern: function (employeeId, reportingManagerId) {
      return request('POST', '/staff/interns',
        { employeeId: employeeId, reportingManagerId: reportingManagerId });
    },

    addManagerIntern: function (managerId, internId) {
      return request('POST',
        '/staff/employees/' + encodeURIComponent(managerId) + '/interns',
        { internId: internId });
    },

    listStaffEmployees: function () {
      return request('GET', '/staff/employees').then(function (payload) {
        return payload.employees;
      });
    },

    markOwnAttendance: function (values) {
      return request('PUT', '/attendance/me/today', values);
    },

    getOwnAttendance: function (month) {
      var path = '/attendance/me';
      if (month) path += '?month=' + encodeURIComponent(month);
      return request('GET', path).catch(function (error) {
        if (error.status === 404) return null;
        throw error;
      });
    },

    getAttendanceSheet: function (month) {
      return request('GET', '/attendance/sheet?month=' + encodeURIComponent(month));
    },

    updateEmployeeAttendance: function (employeeId, date, values) {
      return request('PUT',
        '/attendance/' + encodeURIComponent(employeeId) + '/' + encodeURIComponent(date),
        values);
    },

    downloadAttendanceCsv: function (month) {
      return request('GET', '/attendance/sheet.csv?month=' + encodeURIComponent(month),
        undefined, false, 'blob');
    },

    /** All interns, or - with managerId - only the ones reporting to them. */
    listInterns: function (managerId) {
      var path = '/staff/interns';
      if (managerId) path += '?managerId=' + encodeURIComponent(managerId);
      return request('GET', path).then(function (payload) {
        return payload.interns;
      });
    },

    removeManagerIntern: function (managerId, internId) {
      return request('DELETE',
        '/staff/employees/' + encodeURIComponent(managerId) +
        '/interns/' + encodeURIComponent(internId));
    },

    setInternManager: function (internId, reportingManagerId) {
      return request('PUT', '/staff/interns/' + encodeURIComponent(internId) + '/manager',
        { reportingManagerId: reportingManagerId });
    },

    deleteOnboardingRecord: function (employeeId) {
      return request('DELETE', '/onboarding/' + encodeURIComponent(employeeId));
    },

    restoreOnboarding: function (employeeId) {
      return request('POST', '/onboarding/restore', { employeeId: employeeId });
    },

    deleteStaffEmployee: function (employeeId) {
      return request('DELETE', '/staff/employees/' + encodeURIComponent(employeeId));
    },

    deleteStaffIntern: function (employeeId) {
      return request('DELETE', '/staff/interns/' + encodeURIComponent(employeeId));
    },

    /**
     * "Move to main employee dashboard" / "Move to intern dashboard" - the
     * frontend half of the promote sequence. `employee` is the onboarding
     * record; `reportingManagerId` is required only when it is an intern.
     *
     * Each step tags a rejection with `error.step`, naming which call in the
     * sequence failed - app.js uses that to tell the user what actually
     * happened rather than a bare "something went wrong". The sequence is
     * always safe to run again from the start: every step is idempotent, and
     * the destructive one (removing the onboarding row) is always last and
     * refuses to run until the copy it depends on already exists - see
     * handlers/delete_onboarding_record.py.
     */
    promote: function (employee, reportingManagerId) {
      function step(name, promise) {
        return promise.catch(function (error) {
          error.step = name;
          throw error;
        });
      }

      var copy = employee.employmentType === 'Intern'
        ? step('promote', App.store.promoteToIntern(employee.id, reportingManagerId))
          .then(function () {
            return step('link', App.store.addManagerIntern(reportingManagerId, employee.id));
          })
        : step('promote', App.store.promoteToEmployee(employee.id));

      return copy.then(function () {
        return step('remove', App.store.deleteOnboardingRecord(employee.id));
      });
    },

    /**
     * "Undo move" - the reverse sequence. `staffRecord` is the employee or
     * intern object as read off the employee/intern dashboard, which is what
     * carries `onboardedAt` (the undo window) and, for an intern,
     * `reportingManagerId` (which link to remove).
     */
    unpromote: function (staffRecord) {
      function step(name, promise) {
        return promise.catch(function (error) {
          error.step = name;
          throw error;
        });
      }

      var isIntern = staffRecord.employmentType === 'Intern';
      var managerId = staffRecord.reportingManagerId;

      return step('restore', App.store.restoreOnboarding(staffRecord.id)).then(function () {
        var unlink = (isIntern && managerId)
          ? step('unlink', App.store.removeManagerIntern(managerId, staffRecord.id))
          : Promise.resolve();

        return unlink.then(function () {
          return step('remove', isIntern
            ? App.store.deleteStaffIntern(staffRecord.id)
            : App.store.deleteStaffEmployee(staffRecord.id));
        });
      });
    },

    /**
     * HR manually moving an intern to a different reporting manager. New
     * link added before the old one is removed - see set_intern_manager.py
     * for why that order and not the reverse.
     */
    reassignManager: function (intern, newManagerId) {
      function step(name, promise) {
        return promise.catch(function (error) {
          error.step = name;
          throw error;
        });
      }

      return step('reassign', App.store.setInternManager(intern.id, newManagerId))
        .then(function (result) {
          var previousManagerId = result.previousReportingManagerId;

          return step('link', App.store.addManagerIntern(newManagerId, intern.id))
            .then(function () {
              if (!previousManagerId || previousManagerId === newManagerId) return result;
              return step('unlink',
                App.store.removeManagerIntern(previousManagerId, intern.id))
                .then(function () { return result; });
            });
        });
    },

    setChecklistItem: function (employeeId, itemId, done) {
      // Resolves the FULL employee, with status and progress recomputed server
      // side, so the caller can repaint without a follow-up GET.
      return checklistPatch(employeeId, itemId, { done: !!done });
    },

    /**
     * The HR note on one checklist item. Same endpoint as the tick, because the
     * comment is a property of the same item - PATCH changes whichever keys the
     * body carries, so sending only `comment` leaves `done` alone and vice
     * versa. An empty string clears the note.
     */
    setChecklistComment: function (employeeId, itemId, comment) {
      return checklistPatch(employeeId, itemId, {
        comment: comment == null ? '' : String(comment)
      });
    }
  };
})(window.App);
