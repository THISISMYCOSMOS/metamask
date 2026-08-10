(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const terminal = $("terminal-output");
  const form = $("command-form");
  const input = $("command-input");
  const statusRegion = $("status-region");
  const runState = $("run-state");
  const fileInput = $("trace-file-input");
  const reloadButton = $("reload-trace");
  const connectionDot = $("connection-dot");
  const commandHistory = [];
  let historyIndex = 0;
  let running = false;
  let expectedSteps = 0;
  let traceVerified = false;
  let currentTrace = null;

  const text = {
    ready: "\uB300\uAE30",
    loading: "\uAE30\uB85D \uBD88\uB7EC\uC624\uB294 \uC911",
    loaded: "\uAE30\uB85D \uBD88\uB7EC\uC634",
    running: "\uC2E4\uD589 \uC911",
    recorded: "\uAE30\uB85D \uBD88\uB7EC\uC634",
    verified: "\uAC80\uC99D\uB428",
    empty: "\uD45C\uC2DC\uD560 \uD68C\uCC28 \uC5C6\uC74C",
    malformed: "\uAE30\uB85D \uD615\uC2DD \uC624\uB958",
    wrongKind: "cumulative-loss \uAE30\uB85D\uC774 \uC544\uB2D8",
  };

  function appendTerminal(message, kind = "") {
    const line = document.createElement("div");
    line.className = `terminal-line ${kind}`.trim();
    line.textContent = message;
    terminal.append(line);
    terminal.scrollTop = terminal.scrollHeight;
  }

  function setStatus(message, isError = false) {
    statusRegion.textContent = message;
    statusRegion.classList.toggle("error", isError);
  }

  function setRunning(value) {
    running = value;
    input.disabled = value;
    form.querySelector("button").disabled = value;
    runState.classList.toggle("running", value);
    if (value) runState.textContent = text.running;
    if (!value) input.focus();
  }

  function formatUnits(raw, decimals, maximumFraction = decimals) {
    if (raw === undefined || raw === null || !/^-?\d+$/.test(String(raw))) return "\u2014";
    const negative = String(raw).startsWith("-");
    let digits = negative ? String(raw).slice(1) : String(raw);
    digits = digits.padStart(decimals + 1, "0");
    let whole = digits.slice(0, -decimals || undefined);
    let fraction = decimals ? digits.slice(-decimals) : "";
    fraction = fraction.slice(0, maximumFraction).replace(/0+$/, "");
    whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
  }

  function sumIntegerStrings(values) {
    try {
      return values.reduce((sum, value) => sum + BigInt(String(value)), 0n).toString();
    } catch {
      return null;
    }
  }

  function shorten(value, front = 8, back = 6) {
    const source = String(value || "");
    return source.length > front + back + 1 ? `${source.slice(0, front)}\u2026${source.slice(-back)}` : source || "\u2014";
  }

  function replaceWithEmpty(list, message = text.empty) {
    list.replaceChildren();
    const item = document.createElement("li");
    item.className = "empty-row";
    item.textContent = message;
    list.append(item);
  }

  function addDefinition(list, term, value) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value ?? "\u2014";
    list.append(dt, dd);
  }

  function validateTrace(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error(text.malformed);
    if (data.kind !== "cumulative-loss") throw new Error(text.wrongKind);
    if (!data.hashed || typeof data.hashed !== "object") throw new Error(text.malformed);
    if (!Array.isArray(data.hashed.steps)) throw new Error(text.malformed);
    return data;
  }

  function updateProgress(current, total) {
    const safeCurrent = Number.isFinite(Number(current)) ? Math.max(0, Number(current)) : 0;
    const safeTotal = Number.isFinite(Number(total)) ? Math.max(0, Number(total)) : 0;
    $("progress-current").textContent = String(safeCurrent);
    $("progress-total").textContent = String(safeTotal);
    const percent = safeTotal > 0 ? Math.min(100, (safeCurrent / safeTotal) * 100) : 0;
    $("progress-bar").style.width = `${percent}%`;
  }

  function renderTrace(raw, source, verified = false) {
    const trace = validateTrace(raw);
    currentTrace = trace;
    const hashed = trace.hashed;
    const steps = hashed.steps;
    const result = hashed.result && typeof hashed.result === "object" ? hashed.result : {};
    const baseline = hashed.baseline && typeof hashed.baseline === "object" ? hashed.baseline : {};
    const parameters = baseline.parameters && typeof baseline.parameters === "object" ? baseline.parameters : {};
    const expected = Number.isFinite(Number(parameters.stepCount)) ? Number(parameters.stepCount) : steps.length;
    expectedSteps = expected;

    $("source-label").textContent = source;
    const generated = trace.meta?.generatedAtWallClock;
    const generatedDate = generated ? new Date(generated) : null;
    $("generated-time").textContent = generatedDate && !Number.isNaN(generatedDate.valueOf())
      ? generatedDate.toLocaleString("ko-KR")
      : "\u2014";

    updateProgress(steps.length, expected);
    traceVerified = verified;
    runState.textContent = steps.length ? (verified ? text.verified : text.recorded) : text.empty;
    runState.classList.remove("running");
    const verifyTag = $("verify-tag");
    verifyTag.textContent = verified ? "/ VERIFIED" : "";
    verifyTag.classList.toggle("verified", verified);
    $("metric-steps").textContent = `${steps.length} / ${expected}`;
    const transferred = sumIntegerStrings(steps.map((step) => step.transferAmount));
    $("metric-transfer").textContent = transferred === null ? "\u2014" : `${formatUnits(transferred, 6, 2)} USDC`;
    $("metric-start").textContent = result.startingPortfolio?.totalValue1e18
      ? `$${formatUnits(result.startingPortfolio.totalValue1e18, 18, 4)}` : "\u2014";
    $("metric-end").textContent = result.endingPortfolio?.totalValue1e18
      ? `$${formatUnits(result.endingPortfolio.totalValue1e18, 18, 4)}` : "\u2014";
    $("metric-loss").textContent = result.loss ? `$${formatUnits(result.loss, 18, 4)}` : "\u2014";
    $("metric-loss-rate").textContent = result.lossBps !== undefined
      ? `${formatUnits(result.lossBps, 2, 2)}%` : "\u2014";

    renderPeriods(result.periods);
    renderCaveats(baseline.caveats);
    renderChecks(result.checks, verified);
    renderSteps(steps);
    renderTechnical(hashed);
    setStatus(verified ? "\uAC80\uC99D\uB428" : text.loaded);
    connectionDot.classList.add("connected");
  }

  function beginLiveRun() {
    const steps = currentTrace?.hashed?.steps;
    const baseline = currentTrace?.hashed?.baseline;
    const result = currentTrace?.hashed?.result;
    const total = Number(baseline?.parameters?.stepCount) || (Array.isArray(steps) ? steps.length : 20);
    expectedSteps = total;
    traceVerified = false;
    $("source-label").textContent = "local fork / live";
    $("generated-time").textContent = "\u2014";
    $("verify-tag").textContent = "";
    $("verify-tag").classList.remove("verified");
    updateProgress(0, total);
    $("metric-steps").textContent = `0 / ${total}`;
    $("metric-transfer").textContent = "0 USDC";
    $("metric-start").textContent = result?.startingPortfolio?.totalValue1e18
      ? `$${formatUnits(result.startingPortfolio.totalValue1e18, 18, 4)}` : "\u2014";
    $("metric-end").textContent = "\u2014";
    $("metric-loss").textContent = "\u2014";
    $("metric-loss-rate").textContent = "\u2014";
    renderSteps([]);
    renderChecks([], false);
    setStatus("\uB85C\uCEEC \uD3EC\uD06C \uC2E4\uD589 \uC911");
  }

  function showLiveStep(stepNumber) {
    const allSteps = currentTrace?.hashed?.steps;
    const safeStep = Math.max(0, Math.min(Number(stepNumber) || 0, expectedSteps || 20));
    updateProgress(safeStep, expectedSteps || 20);
    $("metric-steps").textContent = `${safeStep} / ${expectedSteps || 20}`;
    if (Array.isArray(allSteps) && allSteps.length) {
      const visible = allSteps.slice(0, Math.min(safeStep, allSteps.length));
      const transferred = sumIntegerStrings(visible.map((step) => step.transferAmount));
      $("metric-transfer").textContent = transferred === null ? "\u2014" : `${formatUnits(transferred, 6, 2)} USDC`;
      renderSteps(visible);
    }
    setStatus(`${safeStep}\uD68C\uCC28 \uCC98\uB9AC\uB428`);
  }

  function renderPeriods(periods) {
    const list = $("periods-list");
    list.replaceChildren();
    if (!Array.isArray(periods) || periods.length === 0) return replaceWithEmpty(list);
    periods.forEach((period) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      const value = document.createElement("small");
      label.textContent = `P${Number(period.periodIndex) + 1} / ${period.count ?? "\u2014"}\uD68C`;
      value.textContent = period.totalTransferredUsdc !== undefined
        ? `${formatUnits(period.totalTransferredUsdc, 6, 2)} USDC` : "\u2014";
      item.append(label, value);
      list.append(item);
    });
  }

  function renderCaveats(caveats) {
    const list = $("caveats-list");
    list.replaceChildren();
    if (!Array.isArray(caveats) || caveats.length === 0) return replaceWithEmpty(list);
    caveats.forEach((caveat) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      const value = document.createElement("small");
      label.textContent = String(caveat.name || "\uC774\uB984 \uC5C6\uC74C");
      value.textContent = `${caveat.termsBytes ?? "\u2014"} bytes`;
      item.append(label, value);
      list.append(item);
    });
  }

  function renderChecks(checks, verified) {
    const list = $("checks-list");
    list.replaceChildren();
    if (!Array.isArray(checks) || checks.length === 0) return replaceWithEmpty(list);
    checks.forEach((check) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      const state = document.createElement("span");
      name.textContent = String(check.name || "\uC774\uB984 \uC5C6\uC74C");
      if (verified) {
        state.className = `check-state ${check.passed === true ? "pass" : "fail"}`;
        state.textContent = check.passed === true ? "PASS" : "FAIL";
      } else {
        state.className = "check-state unverified";
        state.textContent = check.passed === true ? "RECORDED" : "RECORDED FAIL";
      }
      item.append(name, state);
      list.append(item);
    });
  }

  function renderSteps(steps) {
    const list = $("steps-list");
    const template = $("step-template");
    list.replaceChildren();
    if (steps.length === 0) return replaceWithEmpty(list);
    steps.forEach((step) => {
      const fragment = template.content.cloneNode(true);
      const row = fragment.querySelector(".step-row");
      row.querySelector(".step-number").textContent = `#${step.n ?? Number(step.stepIndex) + 1}`;
      row.querySelector(".step-period").textContent = `P${Number(step.periodIndex ?? 0) + 1}`;
      row.querySelector(".step-amount").textContent = `${formatUnits(step.transferAmount, 6, 2)} USDC`;
      row.querySelector(".step-remaining").textContent = `${formatUnits(step.usdcAfter, 6, 2)} left`;
      const status = row.querySelector(".step-status");
      const success = String(step.receiptStatus).toLowerCase() === "success";
      status.className = `step-status ${success ? "success" : "failed"}`;
      status.textContent = success ? "SUCCESS" : String(step.receiptStatus || "UNKNOWN").toUpperCase();
      const hash = row.querySelector(".step-tx-hash");
      hash.textContent = shorten(step.txHash);
      hash.title = String(step.txHash || "");
      const detailList = row.querySelector(".step-detail-list");
      addDefinition(detailList, "transaction", String(step.txHash || "\u2014"));
      addDefinition(detailList, "block", String(step.blockNumber || "\u2014"));
      addDefinition(detailList, "gas", String(step.gasUsed || "\u2014"));
      addDefinition(detailList, "timestamp", String(step.timestamp || "\u2014"));
      list.append(fragment);
    });
  }

  function renderTechnical(hashed) {
    const list = $("technical-list");
    list.replaceChildren();
    addDefinition(list, "chain ID", String(hashed.fork?.chainId ?? "\u2014"));
    addDefinition(list, "fork block", String(hashed.fork?.blockNumber ?? "\u2014"));
    addDefinition(list, "block hash", String(hashed.fork?.blockHash ?? "\u2014"));
    addDefinition(list, "delegation", String(hashed.delegation?.delegationHash ?? "\u2014"));
    addDefinition(list, "framework commit", String(hashed.fork?.delegationFrameworkCommit ?? "\u2014"));
  }

  function showTraceError(error) {
    connectionDot.classList.remove("connected");
    runState.textContent = "\uB85C\uB4DC \uC2E4\uD328";
    setStatus(error instanceof Error ? error.message : text.malformed, true);
  }

  async function loadCommittedTrace(verified = false) {
    setStatus(text.loading);
    try {
      const response = await fetch("/api/trace", { cache: "no-store" });
      if (!response.ok) throw new Error(`trace HTTP ${response.status}`);
      renderTrace(await response.json(), "traces/cumulative-loss.json", verified);
    } catch (error) {
      showTraceError(error);
    }
  }

  async function runServerCommand(command) {
    setRunning(true);
    if (command === "run g3") beginLiveRun();
    else setStatus(`$ ${command}`);
    try {
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-G3-Console": "1" },
        body: JSON.stringify({ command }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      if (!response.body) throw new Error("stream unavailable");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let refresh = false;
      let doneCode = null;
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = done ? "" : lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const record = JSON.parse(line);
          if (record.type === "line") {
            appendTerminal(String(record.text || ""));
            const match = String(record.text || "").match(/\[g3\]\s+step\s+(\d+)\s/);
            if (match) showLiveStep(Number(match[1]));
          }
          if (record.type === "done") {
            doneCode = Number(record.exitCode);
            refresh = record.refreshTrace === true;
          }
        }
        if (done) break;
      }
      const ok = doneCode === 0;
      appendTerminal(`[exit ${doneCode ?? "?"}]`, ok ? "success" : "error");
      const commandVerifiedTrace = ok && (command === "validate" || command === "run g3");
      if (refresh) await loadCommittedTrace(commandVerifiedTrace);
      if (ok) {
        setStatus(commandVerifiedTrace ? "\uAC80\uC99D\uB428" : "\uC644\uB8CC");
      } else {
        runState.textContent = "\uC2E4\uD589 \uC2E4\uD328";
        setStatus("\uC2E4\uD589 \uC2E4\uD328", true);
      }
    } catch (error) {
      appendTerminal(error instanceof Error ? error.message : "command failed", "error");
      setStatus(error instanceof Error ? error.message : "command failed", true);
    } finally {
      setRunning(false);
    }
  }

  function showHelp() {
    [
      "help      \uBA85\uB839 \uBAA9\uB85D \uBCF4\uAE30",
      "clear     \uCF58\uC194 \uC9C0\uC6B0\uAE30",
      "reload    \uC800\uC7A5\uB41C \uAE30\uB85D \uB2E4\uC2DC \uBD88\uB7EC\uC624\uAE30",
      "status    \uB85C\uCEEC \uAE30\uB85D \uC0C1\uD0DC \uD655\uC778",
      "validate  \uB204\uC801 \uC190\uC2E4 \uAE30\uB85D \uAC80\uC99D",
      "evaluate  \uC815\uCC45 \uD310\uC815 \uC2E4\uD589",
      "run g3     \uC0C8 \uB85C\uCEEC \uD3EC\uD06C\uC5D0\uC11C G3 \uC2E4\uD589",
    ].forEach((line) => appendTerminal(line, "dim"));
  }

  async function execute(raw) {
    const command = raw.trim().toLowerCase();
    if (!command || running) return;
    appendTerminal(`> ${raw.trim()}`, "command");
    commandHistory.push(raw.trim());
    historyIndex = commandHistory.length;
    if (command === "help") return showHelp();
    if (command === "clear") return terminal.replaceChildren();
    if (command === "reload") return loadCommittedTrace(false);
    if (!["status", "validate", "evaluate", "run g3"].includes(command)) {
      appendTerminal("\uC54C \uC218 \uC5C6\uB294 \uBA85\uB839. help\uB97C \uC785\uB825\uD558\uC138\uC694.", "error");
      return;
    }
    await runServerCommand(command);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value;
    input.value = "";
    void execute(value);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      form.requestSubmit();
      return;
    }
    if (event.ctrlKey && event.key.toLowerCase() === "l") {
      event.preventDefault();
      terminal.replaceChildren();
      return;
    }
    if (event.key === "ArrowUp" && commandHistory.length) {
      event.preventDefault();
      historyIndex = Math.max(0, historyIndex - 1);
      input.value = commandHistory[historyIndex] || "";
    }
    if (event.key === "ArrowDown" && commandHistory.length) {
      event.preventDefault();
      historyIndex = Math.min(commandHistory.length, historyIndex + 1);
      input.value = commandHistory[historyIndex] || "";
    }
  });

  reloadButton.addEventListener("click", () => void loadCommittedTrace(false));
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("JSON file is larger than 5 MB");
      renderTrace(JSON.parse(await file.text()), file.name, false);
    } catch (error) {
      showTraceError(error);
    } finally {
      fileInput.value = "";
    }
  });

  appendTerminal("G3 local fork console", "dim");
  appendTerminal(text.ready, "dim");
  void loadCommittedTrace(false);
})();
