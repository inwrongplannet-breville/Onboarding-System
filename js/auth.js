/**
 * The signed-in session, as the browser holds it.
 *
 * What this file is for, and what it is emphatically not for: it decides which
 * *view* to paint, and nothing else. The role stored here is a copy of a claim
 * inside a signed token, kept unpacked so the router does not have to decode a
 * JWT on every hashchange. Editing it in devtools changes what this browser
 * draws and changes nothing at all about what the API will do - every request
 * is authorised by handlers/authorizer.py re-deriving the role from the
 * signature, and the signing key never leaves the backend.
 *
 * That is the reason the guard in app.js is allowed to be as simple as it is.
 * It is a convenience for the user, not a control: getting past it produces an
 * officials screen full of 401s and 403s.
 *
 * sessionStorage, not localStorage. A session that ends with the tab is the
 * right default on a shared HR machine, where the failure mode of localStorage
 * is the next person finding the console already open. Reloading keeps you
 * signed in; closing the tab does not.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  var STORAGE_KEY = 'onboarding.session';

  // Must match ROLES in src/common/accounts.py. Only these two are accepted off
  // storage - anything else reads as signed out rather than as some third kind
  // of user the views have no idea how to render.
  var OFFICIAL = 'official';
  var EMPLOYEE = 'employee';

  var current = null;
  var signedOutHandler = null;

  /**
   * The role claim out of the token itself.
   *
   * This is why the session object no longer has a role field worth editing.
   * The role used to be stored alongside the token, and changing that one string
   * in devtools produced the full officials shell - every button present, every
   * action 403. Harmless, and it looked exactly like a broken app.
   *
   * Reading the claim instead means there is only one place the role can come
   * from. Tampering with it now means tampering with the token, which breaks the
   * signature, which the API answers with a 401 - and a 401 signs you out
   * rather than showing you a console that does not work.
   *
   * To be clear about what this is not: decoding a JWT in the browser is not
   * verifying it. Nothing here checks the signature and nothing here could -
   * the key is in Secrets Manager. This decides which screen to draw. The
   * server decides everything else.
   */
  function roleFromToken(token) {
    var parts = String(token || '').split('.');
    if (parts.length !== 3) return null;

    var claims;
    try {
      // base64url -> base64, and the padding JWT omits put back. atob rejects
      // both the URL alphabet and unpadded input.
      var segment = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      while (segment.length % 4) segment += '=';
      claims = JSON.parse(atob(segment));
    } catch (decodeError) {
      return null;
    }

    if (!claims || typeof claims !== 'object') return null;
    if (claims.role !== OFFICIAL && claims.role !== EMPLOYEE) return null;
    return claims.role;
  }

  function valid(session) {
    return !!session &&
      typeof session.token === 'string' && session.token !== '' &&
      roleFromToken(session.token) !== null;
  }

  /**
   * Reads storage once, then holds the session in memory.
   *
   * Defensive on the way in: this value survives a reload, so it has been
   * outside our control since it was written. Anything that is not a session
   * this build understands - malformed JSON, a role that has since been
   * renamed, a half-written object - is treated as signed out. The cost of
   * being wrong is one extra login; the cost of trusting it is a view rendering
   * against fields that are not there.
   */
  function load() {
    if (current) return current;

    var raw;
    try {
      raw = window.sessionStorage.getItem(STORAGE_KEY);
    } catch (storageError) {
      // Storage can throw outright - Safari's private mode, or a browser
      // configured to block it. Signed out is the honest answer, and it keeps
      // the app usable rather than blank.
      return null;
    }

    if (!raw) return null;

    var parsed = null;
    try {
      parsed = JSON.parse(raw);
    } catch (parseError) {
      parsed = null;
    }

    if (!valid(parsed)) {
      clear();
      return null;
    }

    current = parsed;
    return current;
  }

  function clear() {
    current = null;
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (storageError) {
      // Nothing useful to do. The in-memory copy is gone, which is what the
      // rest of this tab will read.
    }
  }

  function save(session) {
    current = session;
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch (storageError) {
      // The session still works for this page; it just will not survive a
      // reload. Worth nothing louder than this comment - the alternative is
      // refusing a login that otherwise succeeded.
    }
  }

  App.auth = {
    OFFICIAL: OFFICIAL,
    EMPLOYEE: EMPLOYEE,

    session: function () {
      return load();
    },

    token: function () {
      var session = load();
      return session ? session.token : null;
    },

    role: function () {
      var session = load();
      return session ? roleFromToken(session.token) : null;
    },

    isOfficial: function () {
      return App.auth.role() === OFFICIAL;
    },

    isEmployee: function () {
      return App.auth.role() === EMPLOYEE;
    },

    /** Where this role starts, and where the guard sends it back to. */
    home: function () {
      if (App.auth.isOfficial()) return '#/employees';
      if (App.auth.isEmployee()) return '#/directory';
      return '#/login';
    },

    /**
     * Exchanges credentials for a token. Rejects with the store's apiError, so
     * the login view can show `error.message` like every other failure in the
     * app.
     */
    signIn: function (username, password) {
      return App.store.login(username, password).then(function (result) {
        // No role field. It is a claim in the token, and a second copy is a
        // second answer to the same question - see roleFromToken. The response
        // still carries `role`, which is fine for a server to say and pointless
        // for a client to keep.
        var session = {
          token: result.token,
          displayName: result.displayName || username,
          username: username
        };

        if (!valid(session)) {
          // A 200 carrying something we cannot use. Better to fail the login
          // than to store a session that every later read will reject.
          throw new Error('The server returned a session this app cannot use.');
        }

        save(session);
        return session;
      });
    },

    signOut: function () {
      clear();
    },

    /**
     * Called by store.js when the API says 401 - the token expired, or the
     * stack was redeployed with a new signing key while someone was logged in.
     *
     * Distinct from signOut() because it is not something the user did, and the
     * handler gets told so: the app announces "your session expired" rather
     * than silently landing them on a login screen mid-edit.
     */
    expire: function () {
      var wasSignedIn = !!load();
      clear();
      if (wasSignedIn && signedOutHandler) signedOutHandler();
    },

    /** app.js registers the one handler. */
    onExpired: function (handler) {
      signedOutHandler = handler;
    }
  };
})(window.App);
