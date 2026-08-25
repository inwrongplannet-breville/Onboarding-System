/**
 * Data access layer - the seam between the UI and where employees actually live.
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
   * The only place in the app that touches fetch().
   *
   * Two things fetch gets wrong for our purposes, both handled here: it resolves
   * happily on a 500, and response.json() rejects on an empty body - which is
   * exactly what a 204 from DELETE is.
   */
  function request(method, path, body) {
    var options = { method: method, headers: {} };

    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    return fetch(App.API_BASE_URL + path, options).then(function (response) {
      if (response.status === 204) return null;

      return response.text().then(function (text) {
        var payload = null;

        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (parseError) {
            payload = null; // a 502 from API Gateway is HTML; the status still tells the story
          }
        }

        if (!response.ok) throw apiError(response.status, payload);
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
