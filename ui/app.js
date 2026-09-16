(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const terminal = $("terminal-output");
  const intentForm = $("intent-form");
  const intentInput = $("intent-input");
  const approvalForm = $("approval-form");
  const approvalInput = $("approval-input");
  const proposalRevisionSection = $("proposal-revision-section");
  const proposalRevisionForm = $("proposal-revision-form");
  const proposalRevisionFloor = $("proposal-revision-floor");
  const proposalRevisionError = $("proposal-revision-error");
  const executionPlanSection = $("execution-plan-section");
  const executionPlanForm = $("execution-plan-form");
  const executionRecipient = $("execution-recipient");
  const executionAmount = $("execution-amount");
  const executionGas = $("execution-gas");
  const executionSubmit = $("execution-submit");
  const walletConnect = $("wallet-connect");
  const walletStatus = $("wallet-status");
  const walletBinding = $("wallet-binding");
  const verifiedWalletRecord = $("verified-wallet-record");
  const verifiedWallet = $("verified-wallet");
  const verifiedWalletNote = $("verified-wallet-note");
  const receiptEvidence = $("receipt-evidence");
  const broadcastState = $("broadcast-state");
  const structuredForm = $("structured-conditions-form");
  const structuredError = $("structured-error");
  const structuredList = $("structured-condition-list");
  const structuredKindSelect = $("structured-kind-select");
  const structuredAddBtn = $("structured-add-btn");
  let busy = false;
  let walletConfig = { enabled: false };
  let connectedWallet = null;
  let latestState = null;

  const CANONICAL_KINDS = [
    "portfolioValueFloor",
    "portfolioDrawdownCapBps",
    "cumulativeLossCap",
    "cumulativeLossCapBps",
  ];
  const KIND_LABELS = {
    assetBalanceFloor: "ERC-20 잔고 하한",
    portfolioValueFloor: "포트폴리오 최저 가치",
    portfolioDrawdownCapBps: "낙폭 한도 (%)",
    cumulativeLossCap: "누적 손실 한도",
    cumulativeLossCapBps: "누적 손실 한도 (%)",
  };
  const KIND_IDS = {
    portfolioValueFloor: "portfolio-value-floor-structured",
    portfolioDrawdownCapBps: "portfolio-drawdown-cap-bps-structured",
    cumulativeLossCap: "cumulative-loss-cap-structured",
    cumulativeLossCapBps: "cumulative-loss-cap-bps-structured",
  };
  const COMPILER_SOURCE_LABELS = {
    "gemini-api": "Gemini 무료 API",
    "gemini-api-user-revision": "Gemini 원본 + 사용자 수정",
    "gemini-required": "Gemini 응답 필요",
    "offline-fixture": "오프라인 고정 응답",
    "local-structured-editor": "로컬 구조화 편집기",
    "provider-required": "AI 응답 필요",
  };
  const COMPILER_SOURCE_NOTES = {
    "gemini-api": "Gemini Developer API의 구조화 JSON 응답을 로컬 계약으로 다시 검증한 결과입니다. 무료 티어 입력은 Google 제품 개선에 사용될 수 있습니다.",
    "gemini-api-user-revision": "원본 Gemini 제안과 해시는 이력에 보존되고, 현재 값은 사용자가 직접 수정해 새 해시가 생성된 결과입니다.",
    "gemini-required": "자연어 요청을 제출하면 Gemini Developer API를 호출합니다. API 키가 없거나 호출이 실패하면 제안을 만들지 않습니다.",
    "offline-fixture": "저장된 오프라인 테스트 응답과 결합한 결과입니다. 실시간 AI 호출이 아닙니다.",
    "local-structured-editor": "구조화된 조건 편집기가 로컬에서 동일한 규칙으로 생성했습니다. 실시간 AI 해석 결과가 아닙니다.",
    "provider-required": "현재 입력에 결합된 응답이 없습니다. 자연어만으로는 새 제안이 생성되지 않습니다.",
  };

  let structuredConditions = [];

  function defaultStructuredFields(kind) {
    if (kind === "portfolioValueFloor") return { floorValue1e18: "" };
    if (kind === "portfolioDrawdownCapBps") return { referenceValue1e18: "", maxDrawdownBps: "0" };
    if (kind === "cumulativeLossCap") return { windowSeconds: "86400", maxLossValue1e18: "" };
    if (kind === "cumulativeLossCapBps") return { windowSeconds: "86400", maxLossBps: "0" };
    return {};
  }

  function structuredFieldsFromInvariant(invariant) {
    if (invariant.kind === "portfolioValueFloor") {
      return { floorValue1e18: invariant.floorValue1e18 };
    }
    if (invariant.kind === "portfolioDrawdownCapBps") {
      return { referenceValue1e18: invariant.referenceValue1e18, maxDrawdownBps: invariant.maxDrawdownBps };
    }
    if (invariant.kind === "cumulativeLossCap") {
      return { windowSeconds: invariant.windowSeconds, maxLossValue1e18: invariant.maxLossValue1e18 };
    }
    if (invariant.kind === "cumulativeLossCapBps") {
      return { windowSeconds: invariant.windowSeconds, maxLossBps: invariant.maxLossBps };
    }
    return {};
  }

  function bpsToPercentDisplay(bpsValue) {
    if (!/^\d+$/.test(String(bpsValue ?? ""))) return "";
    const percent = Number(bpsValue) / 100;
    return String(percent);
  }

  function percentToBps(percentText) {
    const value = Number(percentText);
    if (!Number.isFinite(value) || percentText.trim() === "") return null;
    const bps = Math.round(value * 100);
    if (bps < 0 || bps > 10000) return null;
    return String(bps);
  }

  function createTextField(labelText, value, onInput, { placeholder = "", helpText = "" } = {}) {
    const wrapper = document.createElement("div");
    wrapper.className = "structured-field";
    const label = document.createElement("label");
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    input.spellcheck = false;
    input.value = value ?? "";
    input.placeholder = placeholder;
    input.addEventListener("input", () => onInput(input.value.trim()));
    wrapper.append(label, input);
    if (helpText) {
      const help = document.createElement("p");
      help.className = "structured-field-help";
      help.textContent = helpText;
      wrapper.append(help);
    }
    return wrapper;
  }

  const BPS_INVALID_MESSAGE = "0~10000 사이의 정수 bps 값이 필요합니다 (예: 20% = 원시값 \"2000\").";

  function createBpsField(fieldName, initialBps, onChange) {
    const wrapper = document.createElement("div");
    wrapper.className = "structured-field structured-bps-field";
    wrapper.dataset.invalid = "0";
    const label = document.createElement("label");
    label.textContent = `${fieldName} (0~10000 bps, 20% = 원시값 "2000")`;
    const row = document.createElement("div");
    row.className = "bps-input-row";
    const errorText = document.createElement("p");
    errorText.className = "bps-field-error";
    errorText.setAttribute("role", "alert");
    const helpText = document.createElement("p");
    helpText.className = "structured-field-help";
    helpText.textContent = "예: 20%를 입력하면 원시 bps 값 2000으로 자동 변환됩니다.";

    const percentInput = document.createElement("input");
    percentInput.type = "text";
    percentInput.inputMode = "decimal";
    percentInput.placeholder = "퍼센트 예: 20";
    percentInput.value = bpsToPercentDisplay(initialBps);

    const bpsInput = document.createElement("input");
    bpsInput.type = "text";
    bpsInput.inputMode = "numeric";
    bpsInput.placeholder = "원시 bps 예: 2000";
    bpsInput.value = initialBps ?? "";

    function setInvalid(isInvalid) {
      wrapper.classList.toggle("field-invalid", isInvalid);
      wrapper.dataset.invalid = isInvalid ? "1" : "0";
      percentInput.classList.toggle("field-invalid", isInvalid);
      bpsInput.classList.toggle("field-invalid", isInvalid);
      errorText.textContent = isInvalid ? BPS_INVALID_MESSAGE : "";
    }

    // On an invalid/cleared percent value, the paired raw bps field and the
    // condition state are cleared too — a stale valid bps value must never
    // be compiled once the visible percent no longer represents it.
    percentInput.addEventListener("input", () => {
      const bps = percentToBps(percentInput.value);
      if (bps === null) {
        bpsInput.value = "";
        setInvalid(true);
        onChange("");
        return;
      }
      bpsInput.value = bps;
      setInvalid(false);
      onChange(bps);
    });
    // On an invalid/cleared raw bps value, propagate the visible raw string
    // itself into condition state so a stale valid value can never survive
    // submission — invalid text fails compilation/server validation instead.
    bpsInput.addEventListener("input", () => {
      const raw = bpsInput.value.trim();
      if (!/^\d{1,5}$/.test(raw) || Number(raw) > 10000) {
        percentInput.value = "";
        setInvalid(true);
        onChange(raw);
        return;
      }
      percentInput.value = bpsToPercentDisplay(raw);
      setInvalid(false);
      onChange(raw);
    });

    row.append(percentInput, bpsInput);
    wrapper.append(label, row, helpText, errorText);
    return wrapper;
  }

  function renderConditionCard(condition) {
    const card = document.createElement("article");
    card.className = "structured-condition-card";

    const heading = document.createElement("div");
    heading.className = "structured-condition-heading";
    const title = document.createElement("strong");
    title.textContent = `${KIND_LABELS[condition.kind]} (${condition.kind})`;
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "structured-remove-btn";
    removeBtn.textContent = "제거";
    removeBtn.addEventListener("click", () => {
      structuredConditions = structuredConditions.filter((entry) => entry !== condition);
      renderStructuredEditor();
    });
    heading.append(title, removeBtn);

    const body = document.createElement("div");
    body.className = "structured-condition-body";
    if (condition.kind === "portfolioValueFloor") {
      body.append(
        createTextField(
          "최저 가치 원시값 (floorValue1e18, USD × 1e18)",
          condition.fields.floorValue1e18,
          (value) => { condition.fields.floorValue1e18 = value; },
          {
            placeholder: "예: 25000000000000000000000",
            helpText: "예: 25,000 USD는 25000000000000000000000으로 입력합니다.",
          },
        ),
      );
    } else if (condition.kind === "portfolioDrawdownCapBps") {
      body.append(
        createTextField(
          "기준 가치 원시값 (referenceValue1e18, 필수, USD × 1e18)",
          condition.fields.referenceValue1e18,
          (value) => {
            condition.fields.referenceValue1e18 = value;
          },
          {
            placeholder: "예: 30000000000000000000000",
            helpText: "예: 기준 가치 30,000 USD는 30000000000000000000000으로 입력합니다.",
          },
        ),
      );
      body.append(
        createBpsField("maxDrawdownBps", condition.fields.maxDrawdownBps, (value) => {
          condition.fields.maxDrawdownBps = value;
        }),
      );
    } else if (condition.kind === "cumulativeLossCap") {
      body.append(
        createTextField(
          "검사 구간(초) (windowSeconds)",
          condition.fields.windowSeconds,
          (value) => { condition.fields.windowSeconds = value; },
          { placeholder: "예: 86400", helpText: "예: 24시간은 86400, 1시간은 3600입니다." },
        ),
      );
      body.append(
        createTextField(
          "최대 손실 원시값 (maxLossValue1e18, USD × 1e18)",
          condition.fields.maxLossValue1e18,
          (value) => { condition.fields.maxLossValue1e18 = value; },
          {
            placeholder: "예: 2000000000000000000000",
            helpText: "예: 최대 손실 2,000 USD는 2000000000000000000000으로 입력합니다.",
          },
        ),
      );
    } else if (condition.kind === "cumulativeLossCapBps") {
      body.append(
        createTextField(
          "검사 구간(초) (windowSeconds)",
          condition.fields.windowSeconds,
          (value) => { condition.fields.windowSeconds = value; },
          { placeholder: "예: 86400", helpText: "예: 24시간은 86400, 1시간은 3600입니다." },
        ),
      );
      body.append(
        createBpsField("maxLossBps", condition.fields.maxLossBps, (value) => {
          condition.fields.maxLossBps = value;
        }),
      );
    }

    card.append(heading, body);
    return card;
  }

  function renderStructuredEditor() {
    structuredList.replaceChildren();
    if (structuredConditions.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "추가된 구조화 조건이 없습니다. 아래 목록에서 조건을 추가하세요.";
      structuredList.append(empty);
    } else {
      structuredConditions
        .slice()
        .sort((a, b) => CANONICAL_KINDS.indexOf(a.kind) - CANONICAL_KINDS.indexOf(b.kind))
        .forEach((condition) => structuredList.append(renderConditionCard(condition)));
    }

    const remaining = remainingStructuredKinds();
    structuredKindSelect.replaceChildren();
    remaining.forEach((kind) => {
      const option = document.createElement("option");
      option.value = kind;
      option.textContent = `${kind} — ${KIND_LABELS[kind]}`;
      structuredKindSelect.append(option);
    });
    updateStructuredAddAvailability();
  }

  function remainingStructuredKinds() {
    const used = new Set(structuredConditions.map((entry) => entry.kind));
    return CANONICAL_KINDS.filter((kind) => !used.has(kind));
  }

  // Always recompute disabled state from busy + remaining kinds so a failed
  // request (busy reset to false) can never leave these controls stuck disabled.
  function updateStructuredAddAvailability() {
    const disabled = busy || remainingStructuredKinds().length === 0;
    structuredKindSelect.disabled = disabled;
    structuredAddBtn.disabled = disabled;
  }

  function syncStructuredEditorFromState(state) {
    const invariants = state.proposal?.policy?.invariants;
    structuredConditions = Array.isArray(invariants)
      ? invariants.map((invariant) => ({ kind: invariant.kind, fields: structuredFieldsFromInvariant(invariant) }))
      : [];
    renderStructuredEditor();
  }

  function appendLog(message, kind = "") {
    const line = document.createElement("div");
    line.className = `terminal-line ${kind}`.trim();
    line.textContent = message;
    terminal.append(line);
    terminal.scrollTop = terminal.scrollHeight;
  }

  function setBusy(value) {
    busy = value;
    intentInput.disabled = value;
    approvalInput.disabled = value;
    intentForm.querySelector("button").disabled = value;
    approvalForm.querySelector("button").disabled = value;
    proposalRevisionForm.querySelector("button").disabled = value;
    proposalRevisionFloor.disabled = value;
    executionPlanForm.querySelector("button").disabled = value;
    executionRecipient.disabled = value;
    executionAmount.disabled = value;
    executionGas.disabled = value;
    walletConnect.disabled = value || !walletConfig.enabled;
    structuredForm.querySelector("button").disabled = value;
    updateStructuredAddAvailability();
  }

  function parseRpcQuantity(value, field) {
    if (typeof value === "number") {
      if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${field} RPC 값이 올바르지 않습니다.`);
      return BigInt(value);
    }
    if (typeof value !== "string" || !/^0x[0-9a-f]+$/i.test(value)) throw new Error(`${field} RPC 값이 올바르지 않습니다.`);
    return BigInt(value);
  }

  function requireAddress(value, field) {
    if (typeof value !== "string" || !/^0x[0-9a-f]{40}$/i.test(value)) throw new Error(`${field} 주소가 올바르지 않습니다.`);
    return value.toLowerCase();
  }

  function encodeBalanceOf(address) {
    return `0x70a08231${requireAddress(address, "지갑").slice(2).padStart(64, "0")}`;
  }

  function encodeTransfer(recipient, amount) {
    const target = requireAddress(recipient, "수취인");
    const value = BigInt(amount);
    if (value <= 0n || value >= 1n << 256n) throw new Error("전송량이 올바르지 않습니다.");
    return `0xa9059cbb${target.slice(2).padStart(64, "0")}${value.toString(16).padStart(64, "0")}`;
  }

  function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function waitForReceipt(provider, transactionHash, timeoutMilliseconds = 180000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMilliseconds) {
      const receipt = await provider.request({
        method: "eth_getTransactionReceipt",
        params: [transactionHash],
      });
      if (receipt) return receipt;
      await delay(1500);
    }
    throw new Error("MetaMask 거래 영수증 대기 시간이 초과되었습니다. 거래 해시로 상태를 다시 확인하세요.");
  }

  function renderWalletPanel() {
    if (!walletConfig.enabled) {
      walletStatus.textContent = "MetaMask 테스트넷 설정 필요";
      walletBinding.textContent = "METAMASK_CHAIN_ID와 테스트 토큰 정보를 .env에 설정하세요.";
      walletConnect.disabled = true;
      return;
    }
    if (!connectedWallet) {
      walletStatus.textContent = "MetaMask 연결 안 됨";
      walletBinding.textContent = `chainId ${walletConfig.chainId} · ${walletConfig.tokenSymbol} ${shorten(walletConfig.tokenAddress)}`;
      walletConnect.textContent = "MetaMask 연결";
      walletConnect.disabled = busy;
      return;
    }
    walletStatus.textContent = `${shorten(connectedWallet.walletAddress)} 연결됨`;
    walletBinding.textContent = `chainId ${connectedWallet.chainId} · ${walletConfig.tokenSymbol} ${shorten(walletConfig.tokenAddress)}`;
    walletConnect.textContent = "연결 다시 확인";
    walletConnect.disabled = busy;
  }

  function setStatus(message, isError = false) {
    const region = $("status-region");
    region.textContent = message;
    region.classList.toggle("error", isError);
  }

  function shorten(value, front = 10, back = 8) {
    const source = String(value || "");
    return source.length > front + back + 1 ? `${source.slice(0, front)}…${source.slice(-back)}` : source || "—";
  }

  function formatTokenAmount(value, decimals, symbol) {
    if (!/^\d+$/.test(String(value ?? "")) || !Number.isInteger(decimals) || decimals < 0) return "—";
    const raw = String(value).padStart(decimals + 1, "0");
    const whole = raw.slice(0, raw.length - decimals) || "0";
    const fraction = decimals ? raw.slice(-decimals).replace(/0+$/, "") : "";
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return `${grouped}${fraction ? `.${fraction}` : ""}${symbol ? ` ${symbol}` : ""}`;
  }

  function explorerTransactionUrl(chainId, transactionHash) {
    if (chainId === 11155111 && /^0x[0-9a-f]{64}$/i.test(transactionHash || "")) {
      return `https://sepolia.etherscan.io/tx/${transactionHash}`;
    }
    return null;
  }

  async function copyText(value, button) {
    if (!value || value === "—") return;
    try {
      await navigator.clipboard.writeText(value);
      const previous = button.textContent;
      button.textContent = "복사됨";
      window.setTimeout(() => { button.textContent = previous; }, 1200);
    } catch {
      setStatus("클립보드 복사에 실패했습니다. 값을 직접 선택해 복사하세요.", true);
    }
  }

  function bpsLabel(value) {
    if (!/^\d+$/.test(String(value ?? ""))) return "—";
    const raw = BigInt(String(value));
    const whole = raw / 100n;
    const fraction = String(raw % 100n).padStart(2, "0").replace(/0+$/, "");
    return `${whole}${fraction ? `.${fraction}` : ""}%`;
  }

  function replaceList(id, values, emptyMessage) {
    const list = $(id);
    list.replaceChildren();
    const rows = Array.isArray(values) && values.length ? values : [emptyMessage];
    rows.forEach((value) => {
      const item = document.createElement("li");
      item.textContent = String(value);
      if (value === emptyMessage) item.className = "muted";
      list.append(item);
    });
  }

  function setFlowStage(id, done, label = "완료", rejected = false) {
    const node = $(id);
    node.classList.toggle("done", done);
    node.classList.toggle("rejected", rejected);
    node.querySelector("strong").textContent = done ? label : "대기";
  }

  function invariantValue(invariant) {
    if (invariant.kind === "assetBalanceFloor") {
      const amount = formatTokenAmount(invariant.assetBalanceFloor, walletConfig.tokenDecimals, walletConfig.tokenSymbol);
      return `최소 잔고 ${amount} · 원시값 ${invariant.assetBalanceFloor} base units`;
    }
    if (invariant.kind === "portfolioValueFloor") return `최저 가치 원시값 ${invariant.floorValue1e18}`;
    if (invariant.kind === "portfolioDrawdownCapBps") {
      return `허용 낙폭 ${bpsLabel(invariant.maxDrawdownBps)} / 원시값 ${invariant.maxDrawdownBps} bps`;
    }
    if (invariant.kind === "cumulativeLossCap") {
      return `검사 구간 ${invariant.windowSeconds}초 / 최대 손실 원시값 ${invariant.maxLossValue1e18}`;
    }
    if (invariant.kind === "cumulativeLossCapBps") {
      return `검사 구간 ${invariant.windowSeconds}초 / 최대 손실률 ${bpsLabel(invariant.maxLossBps)} / 원시값 ${invariant.maxLossBps} bps`;
    }
    return "지원하지 않는 불변조건";
  }

  function renderInvariants(state) {
    const list = $("invariant-list");
    list.replaceChildren();
    const policy = state.proposal?.policy;
    const invariants = policy?.kind === "assetBalanceFloor" ? [policy] : policy?.invariants;
    if (!Array.isArray(invariants) || invariants.length === 0) {
      const allowed = state.request?.allowedInvariants || [];
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = `제안 대기 / 허용 범위: ${allowed.join(", ") || "없음"}`;
      list.append(empty);
      return;
    }
    invariants.forEach((invariant) => {
      const card = document.createElement("article");
      const heading = document.createElement("div");
      const kind = document.createElement("strong");
      const id = document.createElement("code");
      const value = document.createElement("p");
      kind.textContent = `${KIND_LABELS[invariant.kind] || "지원 조건"} (${invariant.kind})`;
      id.textContent = invariant.id || invariant.policyId || "—";
      value.textContent = invariantValue(invariant);
      heading.append(kind, id);
      card.append(heading, value);
      list.append(card);
    });
  }

  function displayEvaluationValue(key, value) {
    if (key.endsWith("Bps")) return `${bpsLabel(value)} / 원시값 ${value} bps`;
    if (["assetBalanceFloor", "observedAfterBalance"].includes(key)) {
      return `${formatTokenAmount(value, walletConfig.tokenDecimals, walletConfig.tokenSymbol)} · 원시값 ${value}`;
    }
    return String(value);
  }

  const EVALUATION_FIELD_LABELS = {
    observedMinimumValue1e18: "관측된 최저 가치",
    floorValue1e18: "허용 최저 가치",
    referenceValue1e18: "기준 가치",
    maxDrawdownBps: "허용 최대 낙폭",
    observedMaximumLossValue1e18: "관측된 최대 손실",
    observedReferenceValue1e18: "관측 기준 가치",
    maxLossValue1e18: "허용 최대 손실",
    maxLossBps: "허용 최대 손실률",
    windowSeconds: "검사 구간(초)",
    assetBalanceFloor: "허용 최소 잔고",
    observedAfterBalance: "예상 전송 후 잔고",
  };

  function localizeRationale(item, invariants) {
    if (item === "The user requested to keep at least 20 USDC in the wallet, which translates to a minimum balance floor of 20 USDC.") {
      return "사용자가 지갑에 최소 20 USDC를 유지하도록 요청해 잔고 하한을 20 USDC로 설정했습니다.";
    }
    if (item === "Given 6 decimals for USDC, 20 USDC equals 20,000,000 base units.") {
      return "USDC의 소수점 6자리를 적용하면 20 USDC는 20,000,000 base units입니다.";
    }
    if (typeof item === "string") return item;
    const invariant = invariants.find((entry) => entry.id === item.invariantId);
    const label = KIND_LABELS[invariant?.kind] || item.invariantId;
    if (item.summary === "The stated minimum portfolio value is represented as USD scaled by 1e18.") {
      return `${label}: 사용자가 지정한 포트폴리오 최저 가치를 USD × 1e18 단위로 반영했습니다.`;
    }
    if (item.summary === "The stated 24-hour loss ceiling is represented as a rolling 86400-second cap.") {
      return `${label}: 사용자가 지정한 24시간 누적 손실 한도를 86,400초 이동 구간으로 반영했습니다.`;
    }
    if (item.summary?.startsWith("Value supplied directly through the structured condition editor")) {
      return `${label}: 구조화된 조건 편집기에서 사용자가 직접 입력한 값을 반영했습니다.`;
    }
    return `${label}: ${item.summary || "별도 근거가 기록되지 않았습니다."}`;
  }

  function localizeAssumption(assumption) {
    if (assumption === "The request implies maintaining a continuous balance floor of 20 USDC.") {
      return "요청을 모든 전송 이후에도 20 USDC 이상의 잔액을 계속 유지해야 한다는 의미로 해석했습니다.";
    }
    if (assumption === "The comma characters are thousands separators and USD amounts use decimal notation.") {
      return "쉼표는 천 단위 구분자로, USD 금액은 소수 표기로 해석했습니다.";
    }
    if (assumption?.startsWith("Structured condition fields supplied through the local editor are authoritative")) {
      return "로컬 편집기에 직접 입력한 조건 값을 기준으로 사용했습니다. 원문 정책은 검토용으로만 보존하며 조건 값을 추론하는 데 사용하지 않았습니다.";
    }
    return assumption;
  }

  function localizeEvaluationReason(reason) {
    const drawdownMismatch = /^portfolioDrawdownCapBps referenceValue1e18 does not match the first portfolio point for invariant (.+)$/.exec(reason || "");
    if (drawdownMismatch) {
      return `낙폭 한도의 기준 가치(referenceValue1e18)가 후보 기록의 첫 포트폴리오 가치와 일치하지 않습니다. 조건 ID: ${drawdownMismatch[1]}`;
    }
    return reason || "후보 평가 입력이 유효하지 않습니다.";
  }

  function localizeEvidence(evidence) {
    const labels = {
      stepIndex: "검사 단계",
      position: "측정 시점",
      startStepIndex: "시작 단계",
      endStepIndex: "종료 단계",
      startTimestamp: "시작 시각",
      endTimestamp: "종료 시각",
    };
    return Object.entries(evidence || {}).map(([key, value]) => {
      const localizedValue = value === "before" ? "실행 전" : value === "after" ? "실행 후" : value;
      return `${labels[key] || key}: ${localizedValue}`;
    }).join(" · ") || "기록 없음";
  }

  function localizeAuditLog(line) {
    if (line === "intent-compiler-request 생성 완료") return "정책 컴파일 요청(intent-compiler-request) 생성 완료";
    if (line === "오프라인 llm-policy-response 스키마 검증 완료") return "오프라인 AI 응답(llm-policy-response) 형식 검증 완료";
    if (line === "request/fork/invariant/rationale 결합 검증 완료") return "요청·검증 블록·조건·근거 결합 검증 완료";
    if (line === "미승인 policy-proposal 생성 완료") return "미승인 정책 제안(policy-proposal) 생성 완료";
    if (line === "proposal hash 승인 요청") return "제안 해시 승인 요청";
    if (line === "정확한 proposalSha256 사용자 승인 기록 완료") return "정확한 제안 해시(proposalSha256) 사용자 승인 기록 완료";
    if (line.startsWith("candidate accepted=true")) return "후보 판정: 조건 충족 — 정책 승인";
    if (line.startsWith("candidate accepted=false")) return "후보 판정: 조건 미충족 — 정책 거절";
    if (line.startsWith("승인 기록 완료 — 후보 평가 입력 무효: ")) {
      return `승인 기록 완료 — 후보 평가 입력 무효: ${localizeEvaluationReason(line.slice("승인 기록 완료 — 후보 평가 입력 무효: ".length))}`;
    }
    return line;
  }

  function renderCandidateEvaluation(evaluation) {
    const section = $("candidate-evaluation");
    if (!evaluation) {
      section.hidden = true;
      return;
    }
    section.hidden = false;

    if (evaluation.status === "evaluation-invalid") {
      const result = $("candidate-result");
      result.textContent = "평가 입력 무효";
      result.className = "candidate-result invalid";
      $("candidate-trace-id").textContent = "—";
      $("candidate-trace-hash").textContent = "—";
      $("evaluation-json").textContent = JSON.stringify(evaluation, null, 2);
      const list = $("evaluation-list");
      list.replaceChildren();
      const note = document.createElement("p");
      note.className = "empty-state";
      note.textContent = localizeEvaluationReason(evaluation.reason);
      list.append(note);
      return;
    }

    const accepted = evaluation.accepted === true;
    const result = $("candidate-result");
    result.textContent = accepted ? "조건 충족" : "조건 미충족";
    result.className = `candidate-result ${accepted ? "accepted" : "rejected"}`;
    $("candidate-trace-id").textContent = evaluation.candidateTraceId || "—";
    $("candidate-trace-hash").textContent = evaluation.candidateTraceSha256 || "—";
    $("evaluation-json").textContent = JSON.stringify(evaluation, null, 2);

    const list = $("evaluation-list");
    list.replaceChildren();
    (evaluation.evaluations || []).forEach((item) => {
      const card = document.createElement("article");
      const heading = document.createElement("div");
      const kind = document.createElement("strong");
      const passed = document.createElement("span");
      const kindLabel = KIND_LABELS[item.kind];
      kind.textContent = kindLabel ? `${kindLabel} (${item.kind})` : item.kind || item.id || "알 수 없는 조건";
      passed.textContent = item.passed === true ? "통과" : "실패";
      passed.className = `evaluation-pass ${item.passed === true ? "pass" : "fail"}`;
      heading.append(kind, passed);

      const values = document.createElement("dl");
      Object.entries(item).forEach(([key, value]) => {
        if (["id", "kind", "passed", "evidence"].includes(key)) return;
        const row = document.createElement("div");
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = EVALUATION_FIELD_LABELS[key] || key;
        dd.textContent = displayEvaluationValue(key, value);
        row.append(dt, dd);
        values.append(row);
      });
      const evidence = document.createElement("pre");
      evidence.textContent = `판정 근거 · ${localizeEvidence(item.evidence)}`;
      card.append(heading, values, evidence);
      list.append(card);
    });
  }

  function renderReceiptEvidence(state) {
    const transaction = state.transaction || {};
    const receipt = transaction.receipt;
    const confirmed = transaction.status === "confirmed" && receipt;
    receiptEvidence.hidden = !confirmed;
    verifiedWalletRecord.hidden = !confirmed;
    if (!confirmed) return;

    const decimals = walletConfig.tokenDecimals;
    const symbol = walletConfig.tokenSymbol;
    const amount = BigInt(receipt.amountBaseUnits);
    const after = BigInt(receipt.assetBalanceAfter);
    $("receipt-balance-before").textContent = formatTokenAmount((after + amount).toString(), decimals, symbol);
    $("receipt-transfer-amount").textContent = `−${formatTokenAmount(amount.toString(), decimals, symbol)}`;
    $("receipt-balance-after").textContent = formatTokenAmount(after.toString(), decimals, symbol);
    $("receipt-sender").textContent = shorten(receipt.from, 12, 10);
    $("receipt-sender").title = receipt.from;
    $("receipt-recipient").textContent = shorten(receipt.recipientAddress, 12, 10);
    $("receipt-recipient").title = receipt.recipientAddress;
    $("receipt-block").textContent = receipt.blockNumber || "—";
    $("receipt-transaction-hash").textContent = receipt.transactionHash || "—";
    $("receipt-audit-hash").textContent = receipt.receiptSha256 || "—";

    const explorerUrl = explorerTransactionUrl(state.proposal?.policy?.chainId, receipt.transactionHash);
    const explorerLink = $("receipt-explorer-link");
    explorerLink.hidden = !explorerUrl;
    if (explorerUrl) explorerLink.href = explorerUrl;

    verifiedWallet.textContent = shorten(receipt.from, 12, 10);
    verifiedWallet.title = receipt.from;
    verifiedWalletNote.textContent = `block ${receipt.blockNumber}에서 ${formatTokenAmount(after.toString(), decimals, symbol)} 사후 잔액 검증 완료`;
  }

  function renderState(state, { addLogs = true } = {}) {
    latestState = state;
    const hasProposal = Boolean(state.proposal);
    const approved = Boolean(state.approval);
    const policy = state.proposal?.policy;
    const coreBinding = policy?.kind === "assetBalanceFloor";
    const evaluation = state.candidateEvaluation;
    const evaluationInvalid = evaluation?.status === "evaluation-invalid";
    const rejected = evaluation?.accepted === false;
    const transaction = state.transaction || {};
    const confirmed = transaction.status === "confirmed";
    const submitted = transaction.status === "submitted";
    const metamaskMode = transaction.mode === "metamask" || (
      walletConfig.enabled && connectedWallet && state.proposal?.policy?.walletAddress?.toLowerCase() === connectedWallet.walletAddress
    );
    const stageLabel = confirmed
      ? "MetaMask 영수증 확인 완료"
      : submitted
      ? metamaskMode ? "MetaMask 테스트넷 제출됨" : "로컬 전송 제출됨"
      : evaluationInvalid
      ? "승인 기록됨 — 평가 입력 무효"
      : rejected
        ? "후보 거절"
        : approved
          ? "승인 기록됨"
          : hasProposal
            ? "검토 필요"
            : "LLM 응답 대기";

    $("stage-badge").textContent = stageLabel;
    $("stage-badge").className = `stage-badge ${confirmed || submitted ? "approved" : evaluationInvalid ? "invalid" : rejected ? "rejected" : approved ? "approved" : hasProposal ? "review" : "waiting"}`;
    setStatus(
      confirmed
        ? "테스트넷 영수증과 ERC-20 Transfer 이벤트, 정책 적용 후 잔고 검증을 완료했습니다."
        : submitted
        ? metamaskMode
          ? "결정론적 사전 판정 후 MetaMask가 테스트넷 거래 해시를 반환했습니다. 영수증 검증은 남아 있습니다."
          : "결정론적 게이트 승인 후 로컬 Anvil에 거래를 1회 제출했습니다."
        : evaluationInvalid
        ? `정책 승인은 기록되었지만 후보 평가 입력이 유효하지 않습니다: ${localizeEvaluationReason(evaluation.reason)}`
        : rejected
          ? "승인된 정책이 G3 기반 후보를 거절했습니다. 거래 요청과 지갑 호출은 없습니다."
          : approved
            ? "정책 승인 기록 완료 — 거래 요청은 아직 생성되지 않았습니다."
            : stageLabel,
    );
    setFlowStage("stage-request", Boolean(state.request));
    setFlowStage("stage-response", hasProposal, hasProposal ? "검증됨" : "대기");
    setFlowStage("stage-proposal", hasProposal);
    setFlowStage("stage-approval", approved, approved ? "기록됨" : "대기");
    setFlowStage(
      "stage-preflight",
      Boolean(evaluation),
      evaluation?.accepted === false ? "거절" : "통과",
      evaluation?.accepted === false,
    );
    setFlowStage("stage-wallet-confirm", ["submitted", "confirmed"].includes(transaction.status), "확인됨");
    setFlowStage("stage-submit", ["submitted", "confirmed"].includes(transaction.status), "제출됨");
    setFlowStage("stage-receipt", confirmed, "확인됨");

    $("intent-text").textContent = state.request?.intentText || "—";
    intentInput.value = state.request?.intentText || "";
    $("request-hash").textContent = shorten(state.requestSha256);
    $("request-hash").title = state.requestSha256 || "";
    $("compiler-source").textContent = COMPILER_SOURCE_LABELS[state.compilerSource] || "AI 응답 필요";
    $("compiler-source-note").textContent = COMPILER_SOURCE_NOTES[state.compilerSource] || "";
    renderInvariants(state);
    syncStructuredEditorFromState(state);
    $("policy-json").textContent = hasProposal
      ? JSON.stringify(state.proposal.policy, null, 2)
      : "제안 없음 — 현재 요청에 결합된 LLM 응답이 필요합니다.";

    const proposalInvariants = state.proposal?.policy?.invariants || [];
    replaceList(
      "rationale-list",
      state.proposal?.rationales?.map((item) => localizeRationale(item, proposalInvariants)),
      "검증된 제안 없음",
    );
    replaceList(
      "assumption-list",
      state.proposal?.assumptions?.map(localizeAssumption),
      "기록된 가정 없음",
    );

    const fork = policy?.fork || state.request?.fork || {};
    const chainId = coreBinding ? policy.chainId : fork.chainId;
    const wallet = coreBinding ? policy.walletAddress : fork.blockNumber;
    const token = coreBinding ? policy.tokenAddress : fork.blockHash;
    $("fork-chain").textContent = String(chainId ?? "—");
    $("fork-block").textContent = coreBinding ? shorten(wallet, 10, 8) : String(wallet ?? "—");
    $("fork-block").title = coreBinding ? wallet || "" : "";
    $("fork-hash").textContent = shorten(token, 12, 10);
    $("fork-hash").title = token || "";
    $("proposal-hash").textContent = state.proposalSha256 || "—";
    $("approval-hash").textContent = state.approvalSha256 || "—";
    $("approval-state").textContent = approved ? "승인 기록됨" : "미승인";
    $("approval-state").classList.toggle("approved", approved);
    approvalForm.hidden = !hasProposal || approved;
    approvalInput.placeholder = hasProposal ? `APPROVE ${state.proposalSha256}` : "승인할 제안 없음";
    approvalInput.value = hasProposal && !approved ? `APPROVE ${state.proposalSha256}` : "";
    proposalRevisionSection.hidden = !coreBinding;
    proposalRevisionFloor.value = coreBinding ? policy.assetBalanceFloor : "";
    $("proposal-revision-source-hash").textContent = state.proposalSha256 || "—";
    proposalRevisionError.textContent = "";
    renderCandidateEvaluation(evaluation);
    renderReceiptEvidence(state);

    executionPlanSection.hidden = !approved || ["submitted", "confirmed", "rejected"].includes(transaction.status);
    $("execution-mode-title").textContent = metamaskMode ? "MetaMask 테스트넷 실행" : "로컬 Anvil 제어 실행";
    $("execution-mode-copy").textContent = metamaskMode
      ? "MetaMask가 제공한 잔고·nonce·eth_call·gas estimate를 정책과 결합해 판정한 뒤, 허용된 정확한 ERC-20 거래만 지갑 확인창으로 보냅니다."
      : "수취인과 전송량을 입력하면 스냅샷 시뮬레이션 후 결정론적 게이트가 허용한 경우에만 로컬 Anvil에 1회 제출합니다.";
    executionSubmit.textContent = metamaskMode ? "사전 검증 후 MetaMask 확인" : "시뮬레이션 후 실행";
    $("broadcast-title").textContent = transaction.status === "confirmed"
      ? "테스트넷 영수증 확인 완료"
      : transaction.status === "submitted"
        ? "테스트넷 영수증 확인 중"
        : transaction.eligibleForBroadcast
          ? metamaskMode ? "MetaMask 사용자 확인 대기" : "브로드캐스트 가능"
          : "브로드캐스트 불가";
    $("broadcast-reason").textContent = transaction.reason || "정확한 거래 요청이 아직 없습니다.";
    broadcastState.className = `broadcast-state ${confirmed ? "confirmed" : submitted ? "submitted" : transaction.eligibleForBroadcast ? "eligible" : rejected ? "rejected" : "blocked"}`;

    if (addLogs) {
      (state.logs || []).forEach((line) => appendLog(localizeAuditLog(line), line.includes("중단") ? "warn" : "success"));
      const stageNames = { "request-created": "요청 생성", "proposal-ready": "제안 준비", approved: "승인 기록", executed: "로컬 전송 제출", "wallet-authorized": "MetaMask 확인 대기", "wallet-submitted": "MetaMask 테스트넷 제출", "wallet-confirmed": "MetaMask 영수증 확인", rejected: "게이트 거절" };
      appendLog(`진행 단계=${stageNames[state.stage] || state.stage} · 요청=${shorten(state.requestSha256)}`, "dim");
    }
    $("connection-dot").classList.add("connected");
  }

  async function requestJson(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  async function connectMetaMask() {
    if (!walletConfig.enabled) throw new Error("MetaMask 테스트넷 바인딩이 서버에 설정되지 않았습니다.");
    const provider = window.ethereum;
    if (!provider || typeof provider.request !== "function") throw new Error("이 브라우저에서 MetaMask를 찾을 수 없습니다.");
    const accounts = await provider.request({ method: "eth_requestAccounts" });
    const chainHex = await provider.request({ method: "eth_chainId" });
    if (!Array.isArray(accounts) || accounts.length === 0) throw new Error("MetaMask 계정이 연결되지 않았습니다.");
    const chainId = Number(parseRpcQuantity(chainHex, "chainId"));
    if (chainId !== walletConfig.chainId) throw new Error(`MetaMask 네트워크를 chainId ${walletConfig.chainId}로 변경하세요.`);
    connectedWallet = { walletAddress: requireAddress(accounts[0], "MetaMask"), chainId };
    renderWalletPanel();
    appendLog(`MetaMask 연결: ${shorten(connectedWallet.walletAddress)} chainId=${chainId}`, "success");
  }

  function invalidateWalletConnection(message) {
    connectedWallet = null;
    renderWalletPanel();
    appendLog(message, "warn");
  }

  function installWalletListeners() {
    const provider = window.ethereum;
    if (!provider || typeof provider.on !== "function") return;
    provider.on("accountsChanged", () => invalidateWalletConnection("MetaMask 계정이 변경되어 정책 바인딩을 다시 생성해야 합니다."));
    provider.on("chainChanged", () => invalidateWalletConnection("MetaMask 네트워크가 변경되어 정책 바인딩을 다시 생성해야 합니다."));
  }

  async function loadState() {
    setBusy(true);
    try {
      appendLog("정책 상태 불러오는 중", "dim");
      walletConfig = await requestJson("/api/wallet/config");
      renderWalletPanel();
      installWalletListeners();
      renderState(await requestJson("/api/policy"));
    } catch (error) {
      const message = error instanceof Error ? error.message : "정책 상태를 불러올 수 없습니다.";
      setStatus(message, true);
      appendLog(message, "error");
    } finally {
      setBusy(false);
      renderWalletPanel();
    }
  }

  walletConnect.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    try {
      await connectMetaMask();
    } catch (error) {
      const message = error instanceof Error ? error.message : "MetaMask 연결 실패";
      setStatus(message, true);
      appendLog(message, "error");
    } finally {
      setBusy(false);
      renderWalletPanel();
    }
  });

  $("copy-transaction-hash").addEventListener("click", (event) => {
    void copyText(latestState?.transaction?.receipt?.transactionHash, event.currentTarget);
  });

  $("copy-receipt-hash").addEventListener("click", (event) => {
    void copyText(latestState?.transaction?.receipt?.receiptSha256, event.currentTarget);
  });

  intentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const intent = intentInput.value.trim();
    if (!intent) return;
    if (walletConfig.enabled && !connectedWallet) {
      setStatus("먼저 MetaMask 테스트넷 지갑을 연결하세요.", true);
      return;
    }
    setBusy(true);
    appendLog(`intent 제출: ${intent}`, "command");
    try {
      const state = await requestJson("/api/policy/intent", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
        body: JSON.stringify({ intent, walletBinding: connectedWallet }),
      });
      terminal.replaceChildren();
      renderState(state);
    } catch (error) {
      const message = error instanceof Error ? error.message : "intent request failed";
      setStatus(message, true);
      appendLog(message, "error");
    } finally {
      setBusy(false);
    }
  });

  approvalForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const confirmation = approvalInput.value.trim();
    setBusy(true);
    appendLog("proposal hash 승인 요청", "command");
    try {
      const state = await requestJson("/api/policy/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
        body: JSON.stringify({ confirmation }),
      });
      approvalInput.value = "";
      renderState(state);
    } catch (error) {
      const message = error instanceof Error ? error.message : "approval failed";
      setStatus(`승인 입력 불일치: ${message}`, true);
      appendLog(`approval invalid: ${message}`, "error");
    } finally {
      setBusy(false);
    }
  });

  proposalRevisionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !latestState?.proposalSha256) return;
    const assetBalanceFloor = proposalRevisionFloor.value.trim();
    if (!/^(0|[1-9][0-9]*)$/.test(assetBalanceFloor)) {
      proposalRevisionError.textContent = "잔고 하한은 앞자리 0이 없는 base-unit 정수여야 합니다.";
      return;
    }
    setBusy(true);
    proposalRevisionError.textContent = "";
    appendLog("LLM 제안의 잔고 하한 사용자 수정", "command");
    try {
      const state = await requestJson("/api/policy/revise", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
        body: JSON.stringify({
          sourceProposalSha256: latestState.proposalSha256,
          assetBalanceFloor,
        }),
      });
      renderState(state);
    } catch (error) {
      const message = error instanceof Error ? error.message : "policy revision failed";
      proposalRevisionError.textContent = message;
      appendLog(message, "error");
    } finally {
      setBusy(false);
    }
  });

  executionPlanForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const recipientAddress = executionRecipient.value.trim();
    const amountBaseUnits = executionAmount.value.trim();
    const gasLimit = executionGas.value.trim() || null;
    setBusy(true);
    appendLog(`예정 거래 제출: recipient=${recipientAddress} amount=${amountBaseUnits}`, "command");
    try {
      const policy = latestState?.proposal?.policy;
      const metamaskMode = Boolean(
        walletConfig.enabled && connectedWallet && policy?.walletAddress?.toLowerCase() === connectedWallet.walletAddress,
      );
      if (walletConfig.enabled && !metamaskMode) throw new Error("현재 승인 정책과 MetaMask 계정이 일치하지 않습니다. 정책을 다시 생성하세요.");
      if (metamaskMode) {
        const provider = window.ethereum;
        const tokenAddress = requireAddress(policy.tokenAddress, "토큰");
        const sender = connectedWallet.walletAddress;
        const transferData = encodeTransfer(recipientAddress, amountBaseUnits);
        const requestBase = { from: sender, to: tokenAddress, value: "0x0", data: transferData };
        const [balanceResult, nonceResult, transferCallResult] = await Promise.all([
          provider.request({ method: "eth_call", params: [{ to: tokenAddress, data: encodeBalanceOf(sender) }, "latest"] }),
          provider.request({ method: "eth_getTransactionCount", params: [sender, "pending"] }),
          provider.request({ method: "eth_call", params: [requestBase, "latest"] }),
        ]);
        const gasHex = gasLimit
          ? `0x${BigInt(gasLimit).toString(16)}`
          : await provider.request({ method: "eth_estimateGas", params: [requestBase] });
        const authorized = await requestJson("/api/policy/wallet/authorize", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
          body: JSON.stringify({
            walletAddress: sender,
            chainId: connectedWallet.chainId,
            recipientAddress,
            amountBaseUnits,
            gasLimit: parseRpcQuantity(gasHex, "gasLimit").toString(),
            assetBalance: parseRpcQuantity(balanceResult, "assetBalance").toString(),
            senderNonce: parseRpcQuantity(nonceResult, "senderNonce").toString(),
            transferCallResult,
          }),
        });
        renderState(authorized);
        if (!authorized.transaction?.eligibleForBroadcast || !authorized.transaction?.walletRequest) return;

        const currentAccounts = await provider.request({ method: "eth_accounts" });
        const currentChain = Number(parseRpcQuantity(await provider.request({ method: "eth_chainId" }), "chainId"));
        if (!Array.isArray(currentAccounts) || currentAccounts[0]?.toLowerCase() !== sender || currentChain !== connectedWallet.chainId) {
          throw new Error("사전 판정 후 MetaMask 계정 또는 네트워크가 변경되었습니다. 전송을 중단합니다.");
        }
        const [freshBalance, freshNonce] = await Promise.all([
          provider.request({ method: "eth_call", params: [{ to: tokenAddress, data: encodeBalanceOf(sender) }, "latest"] }),
          provider.request({ method: "eth_getTransactionCount", params: [sender, "pending"] }),
        ]);
        if (
          parseRpcQuantity(freshBalance, "assetBalance") !== parseRpcQuantity(balanceResult, "assetBalance")
          || parseRpcQuantity(freshNonce, "senderNonce") !== parseRpcQuantity(nonceResult, "senderNonce")
        ) {
          throw new Error("사전 판정 후 잔고 또는 nonce가 변경되었습니다. 전송을 중단하고 다시 판정하세요.");
        }
        appendLog("MetaMask 사용자 거래 확인 요청", "command");
        const transactionHash = await provider.request({
          method: "eth_sendTransaction",
          params: [authorized.transaction.walletRequest],
        });
        const submitted = await requestJson("/api/policy/wallet/submitted", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
          body: JSON.stringify({ planSha256: authorized.transaction.planSha256, transactionHash }),
        });
        renderState(submitted);
        appendLog("테스트넷 영수증 대기", "dim");
        const receipt = await waitForReceipt(provider, transactionHash);
        const receiptBlock = receipt.blockNumber;
        if (typeof receiptBlock !== "string") throw new Error("MetaMask 영수증에 blockNumber가 없습니다.");
        const confirmedBalance = await provider.request({
          method: "eth_call",
          params: [{ to: tokenAddress, data: encodeBalanceOf(sender) }, receiptBlock],
        });
        const confirmed = await requestJson("/api/policy/wallet/confirmed", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
          body: JSON.stringify({
            planSha256: authorized.transaction.planSha256,
            transactionHash,
            receipt,
            assetBalanceAfter: parseRpcQuantity(confirmedBalance, "assetBalanceAfter").toString(),
          }),
        });
        renderState(confirmed);
      } else {
        const state = await requestJson("/api/policy/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
          body: JSON.stringify({ recipientAddress, amountBaseUnits, gasLimit }),
        });
        renderState(state);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "controlled execution failed";
      setStatus(message, true);
      appendLog(message, "error");
    } finally {
      setBusy(false);
    }
  });

  structuredAddBtn.addEventListener("click", () => {
    const kind = structuredKindSelect.value;
    if (!kind) return;
    structuredConditions.push({ kind, fields: defaultStructuredFields(kind) });
    renderStructuredEditor();
  });

  structuredForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    structuredError.textContent = "";
    if (structuredConditions.length === 0) {
      structuredError.textContent = "최소 한 개의 구조화 조건이 필요합니다.";
      return;
    }
    const invalidField = structuredList.querySelector('.structured-bps-field[data-invalid="1"]');
    if (invalidField) {
      structuredError.textContent = "하나 이상의 bps 필드 값이 올바르지 않습니다. 값을 수정한 뒤 다시 제출하세요.";
      invalidField.querySelector("input.field-invalid")?.focus();
      return;
    }
    const invariants = CANONICAL_KINDS.filter((kind) => structuredConditions.some((entry) => entry.kind === kind)).map(
      (kind) => {
        const condition = structuredConditions.find((entry) => entry.kind === kind);
        return { id: KIND_IDS[kind], kind, ...condition.fields };
      },
    );
    const intent = intentInput.value.trim() || "구조화된 조건 편집기로 생성된 정책 (자연어 미입력)";
    setBusy(true);
    appendLog("구조화된 조건 제출", "command");
    try {
      const state = await requestJson("/api/policy/conditions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Policy-Console": "1" },
        body: JSON.stringify({ intent, invariants }),
      });
      terminal.replaceChildren();
      renderState(state);
    } catch (error) {
      const message = error instanceof Error ? error.message : "structured condition submit failed";
      structuredError.textContent = message;
      appendLog(message, "error");
    } finally {
      setBusy(false);
    }
  });

  void loadState();
})();
