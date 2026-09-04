(() => {
  "use strict";

  const docs = window.API_DOCS;
  const tokenKey = "onboard-api-docs-token";
  const roleKey = "onboard-api-docs-role";
  const displayKey = "onboard-api-docs-display";
  const tagOrder = ["Authentication", "Employees", "Documents", "Checklist", "Staff", "Lifecycle"];
  const tagIcons = { Authentication: "A", Employees: "E", Documents: "D", Checklist: "C", Staff: "S", Lifecycle: "L" };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const pretty = (value) => JSON.stringify(value, null, 2);

  let toastTimer;
  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("show"), 2800);
  }

  function currentSession() {
    return {
      token: sessionStorage.getItem(tokenKey) || "",
      role: sessionStorage.getItem(roleKey) || "",
      displayName: sessionStorage.getItem(displayKey) || ""
    };
  }

  function setSession(payload) {
    sessionStorage.setItem(tokenKey, payload.token);
    sessionStorage.setItem(roleKey, payload.role || "authenticated");
    sessionStorage.setItem(displayKey, payload.displayName || payload.role || "Connected");
    updateAuthState();
  }

  function clearSession() {
    sessionStorage.removeItem(tokenKey);
    sessionStorage.removeItem(roleKey);
    sessionStorage.removeItem(displayKey);
    updateAuthState();
  }

  function updateAuthState() {
    const state = $("#auth-state");
    const session = currentSession();
    if (session.token) {
      state.className = "status-pill connected";
      state.textContent = `${session.displayName} · disconnect`;
      state.title = "Click to remove this tab's token";
      state.style.cursor = "pointer";
    } else {
      state.className = "status-pill neutral";
      state.textContent = "Not connected";
      state.title = "";
      state.style.cursor = "default";
    }
  }

  function responseChips(responses) {
    return Object.entries(responses).map(([code, label]) =>
      `<span class="response-chip"><strong>${escapeHtml(code)}</strong>${escapeHtml(label)}</span>`
    ).join("");
  }

  function paramFields(endpoint) {
    if (!endpoint.params?.length) return "";
    return `<div class="console-fields">${endpoint.params.map((param) => `
      <label>${escapeHtml(param.name)} <span>${escapeHtml(param.in)}${param.required ? " · required" : " · optional"}</span>
        <input data-param="${escapeHtml(param.name)}" data-in="${escapeHtml(param.in)}" value="${escapeHtml(param.example)}" placeholder="${escapeHtml(param.description)}">
      </label>`).join("")}</div>`;
  }

  function renderEndpoint(endpoint) {
    const lower = endpoint.method.toLowerCase();
    const body = endpoint.body === undefined ? "" : `
      <p class="detail-title">JSON BODY</p>
      <textarea class="body-editor" spellcheck="false" aria-label="JSON request body">${escapeHtml(pretty(endpoint.body))}</textarea>`;
    const warning = endpoint.mutates ? `
      <label class="danger-confirm">
        <input type="checkbox" class="confirm-live">
        <span><strong>${endpoint.danger ? "Destructive live operation." : "Live write operation."}</strong> I understand this request targets the deployed AWS <code>dev</code> stage and may change its data.</span>
      </label>` : "";
    const inputNotes = endpoint.params?.length ? `
      <div class="input-notes">
        ${endpoint.params.map((param) => `<span><code>${escapeHtml(param.name)}</code><b>${escapeHtml(param.in)}${param.required ? " · required" : " · optional"}</b>${escapeHtml(param.description)}</span>`).join("")}
      </div>` : "";

    return `
      <details class="endpoint-card" id="${escapeHtml(endpoint.id)}" data-search="${escapeHtml(`${endpoint.method} ${endpoint.path} ${endpoint.summary} ${endpoint.tag}`.toLowerCase())}">
        <summary class="endpoint-summary">
          <span class="method ${lower}">${endpoint.method}</span>
          <code class="endpoint-path">${escapeHtml(endpoint.path)}</code>
          <span class="endpoint-name">${escapeHtml(endpoint.summary)}</span>
          <span class="endpoint-meta">
            ${endpoint.danger ? '<span class="danger-badge">CAUTION</span>' : ""}
            <span class="access-badge">${escapeHtml(endpoint.access)}</span>
            <span class="chevron">⌄</span>
          </span>
        </summary>
        <div class="endpoint-detail">
          <p class="endpoint-description">${escapeHtml(endpoint.description)}</p>
          <div class="detail-grid">
            <div><p class="detail-title">EXECUTION FLOW</p><div class="execution-flow">${endpoint.flow.map((step, index) => `${index ? '<span class="flow-arrow">→</span>' : ""}<span class="flow-step">${escapeHtml(step)}</span>`).join("")}</div></div>
            <div><p class="detail-title">RESPONSES</p><div class="response-list">${responseChips(endpoint.responses)}</div></div>
          </div>
          ${inputNotes}
          <details class="example-disclosure">
            <summary>Expected ${escapeHtml(endpoint.success)} response example</summary>
            <pre>${escapeHtml(pretty(endpoint.example))}</pre>
          </details>
          <button class="try-toggle" type="button">Try it against AWS</button>
          <div class="console" hidden>
            <div class="console-head"><strong>Live request console</strong><span>Target: eu-north-1 / dev</span></div>
            <div class="console-body">
              <div class="request-pane">
                ${paramFields(endpoint)}
                <div class="request-url"><b>${endpoint.method}</b><span class="computed-url">${escapeHtml(docs.baseUrl + endpoint.path)}</span></div>
                ${body}
                ${warning}
                <button class="send-button" type="button" ${endpoint.mutates ? "disabled" : ""}>Send request</button>
              </div>
              <div class="response-pane">
                <div class="response-status"><strong>RESPONSE</strong><span class="timing"></span></div>
                <pre class="response-output"><span class="empty-response">Run the request to inspect the exact URL, headers, status, and JSON response.</span></pre>
              </div>
            </div>
          </div>
        </div>
      </details>`;
  }

  function renderEndpoints() {
    const list = $("#endpoint-list");
    list.innerHTML = tagOrder.map((tag) => {
      const children = docs.endpoints.filter((endpoint) => endpoint.tag === tag).map(renderEndpoint).join("");
      return `<section class="tag-block" data-tag="${escapeHtml(tag)}"><h3 class="tag-heading"><span>${tagIcons[tag]}</span>${tag}</h3>${children}</section>`;
    }).join("");

    $("#endpoint-nav").innerHTML = tagOrder.map((tag) => {
      const links = docs.endpoints.filter((endpoint) => endpoint.tag === tag).map((endpoint) => `
        <a class="nav-link" href="#${escapeHtml(endpoint.id)}"><span class="mini-method ${endpoint.method.toLowerCase()}">${endpoint.method}</span><span class="mini-path">${escapeHtml(endpoint.path)}</span></a>`).join("");
      return `<div class="nav-group"><div class="nav-group-title">${tag}</div>${links}</div>`;
    }).join("");
  }

  function renderFunctionCall(call) {
    return `<div class="function-call${call.external ? " external-call" : ""}">
      <span class="method ${call.method.toLowerCase()}">${escapeHtml(call.method)}</span>
      <div><code>${escapeHtml(call.path)}</code>${call.note ? `<small>${escapeHtml(call.note)}</small>` : ""}</div>
      ${call.external ? '<b class="service-badge">DIRECT S3</b>' : ""}
    </div>`;
  }

  function renderFunctionStep(step, index) {
    const flags = `${step.parallel ? " parallel-step" : ""}${step.optional ? " optional-step" : ""}`;
    const label = step.parallel ? "PARALLEL" : step.optional ? "OPTIONAL" : `STEP ${index + 1}`;
    const content = step.local
      ? `<div class="local-call"><span>LOCAL</span><code>${escapeHtml(step.local)}</code></div>`
      : `<div class="function-calls${step.parallel ? " parallel-calls" : ""}">${step.calls.map(renderFunctionCall).join("")}</div>`;
    return `<div class="function-step${flags}"><span class="step-label">${label}</span>${content}</div>`;
  }

  function renderFunctionMap() {
    const categories = [...new Set(docs.functionFlows.map((flow) => flow.category))];
    $("#function-map").innerHTML = categories.map((category, categoryIndex) => {
      const flows = docs.functionFlows.filter((flow) => flow.category === category).map((flow) => `
        <article class="function-card">
          <header>
            <span class="function-index">${String(docs.functionFlows.indexOf(flow) + 1).padStart(2, "0")}</span>
            <div><h4>${escapeHtml(flow.title)}</h4><p>${escapeHtml(flow.description)}</p></div>
            <span class="actor-badge">${escapeHtml(flow.actor)}</span>
          </header>
          <div class="function-sequence">${flow.steps.map(renderFunctionStep).join('<span class="sequence-connector" aria-hidden="true">→</span>')}</div>
        </article>`).join("");
      return `<section class="function-category"><div class="function-category-title"><span>${String(categoryIndex + 1).padStart(2, "0")}</span><h3>${escapeHtml(category)}</h3></div><div class="function-card-grid">${flows}</div></section>`;
    }).join("");

    $("#store-function-index").innerHTML = docs.storeFunctions.map((item, index) => `
      <div class="store-function-row"><span>${String(index + 1).padStart(2, "0")}</span><code class="store-name">App.store.${escapeHtml(item.name)}()</code><code class="store-sequence">${escapeHtml(item.sequence)}</code></div>`).join("");
    $("#flow-count").textContent = docs.functionFlows.length;
    $("#store-count").textContent = docs.storeFunctions.length;
  }

  function buildUrl(endpoint, card) {
    let path = endpoint.path;
    const query = new URLSearchParams();
    $$('[data-param]', card).forEach((input) => {
      const value = input.value.trim();
      if (input.dataset.in === "path") path = path.replace(`{${input.dataset.param}}`, encodeURIComponent(value));
      if (input.dataset.in === "query" && value) query.set(input.dataset.param, value);
    });
    return docs.baseUrl + path + (query.size ? `?${query}` : "");
  }

  function updateComputedUrl(endpoint, card) {
    $(".computed-url", card).textContent = buildUrl(endpoint, card);
  }

  function headersObject(headers) {
    const object = {};
    headers.forEach((value, key) => { object[key] = value; });
    return object;
  }

  async function sendEndpoint(endpoint, card) {
    const output = $(".response-output", card);
    const timing = $(".timing", card);
    const send = $(".send-button", card);
    const url = buildUrl(endpoint, card);
    const session = currentSession();

    if (endpoint.access !== "Public" && !session.token) {
      output.textContent = "No bearer token is available. Connect with the demo credentials above, then retry.";
      toast("Connect first to call an authenticated route.");
      return;
    }

    const headers = { Accept: "application/json" };
    const options = { method: endpoint.method, headers };
    let parsedBody;

    if (endpoint.body !== undefined) {
      try {
        parsedBody = JSON.parse($(".body-editor", card).value);
      } catch (error) {
        output.textContent = `Request not sent: invalid JSON\n\n${error.message}`;
        return;
      }
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(parsedBody);
    }
    if (endpoint.access !== "Public") headers.Authorization = `Bearer ${session.token}`;

    const visibleRequest = {
      method: endpoint.method,
      url,
      headers: { ...headers, ...(headers.Authorization ? { Authorization: "Bearer [token from this tab]" } : {}) },
      ...(parsedBody === undefined ? {} : { body: parsedBody })
    };

    send.disabled = true;
    send.textContent = "Sending…";
    output.textContent = pretty({ request: visibleRequest, response: "Waiting for AWS…" });
    timing.textContent = "";
    const started = performance.now();

    try {
      const response = await fetch(url, options);
      const elapsed = Math.round(performance.now() - started);
      const raw = await response.text();
      let responseBody = raw;
      try { responseBody = raw ? JSON.parse(raw) : null; } catch (_) { /* retain text */ }

      if (response.ok && responseBody?.token) setSession(responseBody);
      if (response.status === 401 && endpoint.id !== "post-login") clearSession();

      output.textContent = pretty({
        request: visibleRequest,
        response: {
          status: response.status,
          statusText: response.statusText,
          headers: headersObject(response.headers),
          body: responseBody
        }
      });
      timing.textContent = `${response.status} · ${elapsed} ms`;
      toast(response.ok ? `${endpoint.method} completed with ${response.status}` : `AWS returned ${response.status}`);
    } catch (error) {
      const elapsed = Math.round(performance.now() - started);
      output.textContent = pretty({
        request: visibleRequest,
        response: {
          error: error.message,
          hint: "Serve this folder at http://localhost:8001. The deployed CORS policy must allow that exact origin."
        }
      });
      timing.textContent = `Network error · ${elapsed} ms`;
    } finally {
      send.textContent = "Send request";
      send.disabled = endpoint.mutates && !$(".confirm-live", card)?.checked;
    }
  }

  async function login(event) {
    event.preventDefault();
    const button = $("#login-button");
    const body = { username: $("#login-username").value, password: $("#login-password").value };
    button.disabled = true;
    button.querySelector("span").textContent = "Connecting…";
    try {
      const response = await fetch(`${docs.baseUrl}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body)
      });
      const payload = await response.json();
      if (!response.ok || !payload.token) throw new Error(payload.error?.message || `AWS returned ${response.status}`);
      setSession(payload);
      toast(`Connected as ${payload.displayName} (${payload.role})`);
    } catch (error) {
      clearSession();
      const state = $("#auth-state");
      state.className = "status-pill error";
      state.textContent = "Connection failed";
      toast(error.message + (location.origin !== "http://localhost:8001" ? " · Use http://localhost:8001" : ""));
    } finally {
      button.disabled = false;
      button.querySelector("span").textContent = "Connect to AWS";
    }
  }

  function bindEndpointEvents() {
    docs.endpoints.forEach((endpoint) => {
      const card = document.getElementById(endpoint.id);
      const toggle = $(".try-toggle", card);
      const consoleNode = $(".console", card);
      toggle.addEventListener("click", () => {
        consoleNode.hidden = !consoleNode.hidden;
        toggle.textContent = consoleNode.hidden ? "Try it against AWS" : "Close request console";
        if (!consoleNode.hidden) updateComputedUrl(endpoint, card);
      });
      $$('[data-param]', card).forEach((input) => input.addEventListener("input", () => updateComputedUrl(endpoint, card)));
      const confirm = $(".confirm-live", card);
      if (confirm) confirm.addEventListener("change", () => { $(".send-button", card).disabled = !confirm.checked; });
      $(".send-button", card).addEventListener("click", () => sendEndpoint(endpoint, card));
    });
  }

  function bindSearch() {
    const input = $("#search");
    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();
      let shown = 0;
      $$(".endpoint-card").forEach((card) => {
        const visible = !query || card.dataset.search.includes(query);
        card.hidden = !visible;
        if (visible) shown += 1;
      });
      $$(".tag-block").forEach((group) => { group.hidden = !$(".endpoint-card:not([hidden])", group); });
      $("#result-count").textContent = `${shown} route${shown === 1 ? "" : "s"}`;
      $("#empty-state").hidden = shown !== 0;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "/" && !document.body.classList.contains("show-functions") && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
        event.preventDefault(); input.focus();
      }
    });
  }

  function bindCredentialSwitch() {
    $$(".role-button").forEach((button) => button.addEventListener("click", () => {
      $$(".role-button").forEach((item) => item.classList.toggle("active", item === button));
      const selected = docs.credentials[button.dataset.role];
      $("#login-username").value = selected.username;
      $("#login-password").value = selected.password;
    }));
  }

  function bindModels() {
    const code = $("#model-code");
    code.textContent = pretty(docs.models.employee);
    $$(".model-tab").forEach((button) => button.addEventListener("click", () => {
      $$(".model-tab").forEach((item) => item.classList.toggle("active", item === button));
      code.textContent = pretty(docs.models[button.dataset.model]);
    }));
  }

  function setDashboard(name, updateUrl = false) {
    const showFunctions = name === "functions";
    document.body.classList.toggle("show-functions", showFunctions);
    $$(".dashboard-button").forEach((button) => {
      const active = button.dataset.dashboard === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    if (updateUrl) {
      const next = showFunctions ? "#functions" : location.pathname + location.search;
      history.replaceState(null, "", next);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  renderEndpoints();
  renderFunctionMap();
  bindEndpointEvents();
  bindSearch();
  bindCredentialSwitch();
  bindModels();
  updateAuthState();

  $$(".dashboard-button").forEach((button) => button.addEventListener("click", () => {
    setDashboard(button.dataset.dashboard, true);
  }));

  $("#login-form").addEventListener("submit", login);
  $("#auth-state").addEventListener("click", () => {
    if (currentSession().token) { clearSession(); toast("Token removed from this tab."); }
  });
  $("#copy-base").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(docs.baseUrl); toast("Base URL copied."); }
    catch (_) { toast("Copy failed. Select the URL manually."); }
  });

  window.addEventListener("hashchange", () => {
    const hash = location.hash.slice(1);
    const target = document.getElementById(hash);
    if (["functions", "function-sequences", "workflows"].includes(hash)) {
      setDashboard("functions");
    } else {
      setDashboard("api");
      if (target?.matches("details")) target.open = true;
    }
  });
  if (location.hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else setDashboard("api");
})();
