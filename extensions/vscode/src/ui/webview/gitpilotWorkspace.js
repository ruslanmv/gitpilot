const vscode = acquireVsCodeApi();

    let currentState = null;
    let lastError = null;
    let busyCount = 0;
    let lastRenderedSignature = "";
    let dynamicHandlerAbortController = null;
    let thinkingStartedAt = 0;
    let thinkingTimerId = null;

    const byId = (id) => document.getElementById(id);

    const esc = (value) =>
      String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");

    const post = (msg) => vscode.postMessage(msg);
    const nowIso = () => new Date().toISOString();

    const formatTime = (iso) => {
      try {
        const date = iso ? new Date(iso) : new Date();
        if (Number.isNaN(date.getTime())) return "";
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch {
        return "";
      }
    };

    const formatElapsed = (ms) => {
      const totalSeconds = Math.max(0, Math.floor(ms / 1000));
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    };

    const statusLabel = (status) => ({
      idle: "Idle",
      planning: "Planning",
      generating: "Generating",
      reviewing: "Reviewing",
      ready_to_apply: "Ready to apply",
      applying: "Applying",
      done: "Done",
      failed: "Failed"
    }[status] || String(status || "idle"));

    const phaseMap = {
      planning:    { icon: "&#9679;", label: "Analyzing your request..." },
      generating:  { icon: "&#9998;", label: "Writing response..." },
      reviewing:   { icon: "&#128269;", label: "Reviewing changes..." },
      applying:    { icon: "&#9889;", label: "Applying changes..." },
    };

    const getPhaseLabel = () => {
      const status = ((currentState || {}).activeTask || {}).status;
      return (phaseMap[status] || {}).label || "GitPilot is thinking...";
    };

    const getPhaseIcon = () => {
      const status = ((currentState || {}).activeTask || {}).status;
      return (phaseMap[status] || {}).icon || "&#9679;";
    };

    const pillClassForStatus = (status) => {
      if (status === "failed") return "pill pill-danger";
      if (status === "ready_to_apply" || status === "done") return "pill pill-success";
      if (status === "planning" || status === "generating" || status === "reviewing" || status === "applying") return "pill pill-accent";
      return "pill";
    };

    const normalizeAssistantText = (text) => {
      const value = String(text || "").trim();
      if (!value) return "GitPilot is ready for your next request.";

      return esc(value)
        .replace(/^###\s*(.+)$/gm, "<h4>$1</h4>")
        .replace(/^##\s*(.+)$/gm, "<h4>$1</h4>")
        .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    };

    const getChatMessages = (state) => {
      const messages = (((state || {}).chat || {}).messages || []).slice();
      if (messages.length > 0) return messages;

      return [{
        id: "assistant:default",
        role: "assistant",
        content: "What would you like GitPilot to do?",
        createdAt: nowIso()
      }];
    };

    const taskIsActive = (state) => {
      if (!state) return false;
      if (state.ui && state.ui.mode === "idle") return false;

      const task = state.activeTask || {};
      return Boolean(
        task.title ||
        (task.plan && task.plan.steps && task.plan.steps.length) ||
        (task.filesInScope && task.filesInScope.length) ||
        (task.changedFiles && task.changedFiles.length) ||
        (task.status && task.status !== "idle")
      );
    };

    const startThinkingTimer = () => {
      if (!thinkingStartedAt) thinkingStartedAt = Date.now();
      stopThinkingTimer();
      thinkingTimerId = window.setInterval(() => {
        const elapsedNode = byId("chat-thinking-elapsed");
        if (elapsedNode && thinkingStartedAt) {
          elapsedNode.textContent = formatElapsed(Date.now() - thinkingStartedAt);
        }
      }, 1000);
    };

    const stopThinkingTimer = () => {
      if (thinkingTimerId) {
        window.clearInterval(thinkingTimerId);
        thinkingTimerId = null;
      }
    };

    const pushOptimisticUserMessage = (text) => {
      if (!currentState) currentState = {};
      if (!currentState.chat) currentState.chat = {};
      if (!Array.isArray(currentState.chat.messages)) currentState.chat.messages = [];

      currentState.chat.messages = [
        ...currentState.chat.messages,
        {
          id: "user:optimistic:" + Date.now(),
          role: "user",
          content: text,
          createdAt: nowIso()
        }
      ];

      render(currentState);
    };

    const collapseThinkingBubble = (callback) => {
      const existing = byId("chat-thinking-item");
      if (!existing) { callback(); return; }
      existing.classList.add("collapsing");
      existing.addEventListener("animationend", () => {
        existing.remove();
        callback();
      }, { once: true });
      setTimeout(() => { if (existing.parentNode) { existing.remove(); callback(); } }, 250);
    };

    const setLoadingIndicator = (visible, text) => {
      const list = byId("chat-list");
      if (!list) return;

      const existing = byId("chat-thinking-item");

      if (!visible) {
        if (existing) {
          collapseThinkingBubble(() => {});
        }
        thinkingStartedAt = 0;
        stopThinkingTimer();
        return;
      }

      if (!thinkingStartedAt) {
        thinkingStartedAt = Date.now();
      }

      const phaseLabel = text || getPhaseLabel();
      const phaseIcon = getPhaseIcon();

      if (existing) {
        const label = existing.querySelector(".thinking-label");
        const elapsed = existing.querySelector("#chat-thinking-elapsed");
        const icon = existing.querySelector(".thinking-phase-icon");

        if (label) label.textContent = phaseLabel;
        if (elapsed) elapsed.textContent = formatElapsed(Date.now() - thinkingStartedAt);
        if (icon) icon.innerHTML = phaseIcon;

        startThinkingTimer();

        requestAnimationFrame(() => {
          list.scrollTop = list.scrollHeight;
        });
        return;
      }

      const item = document.createElement("li");
      item.id = "chat-thinking-item";
      item.className = "chat-item assistant thinking";
      item.innerHTML = `
        <div class="chat-role-row">
          <div class="chat-role">
            <span class="role-avatar assistant">G</span>
            GitPilot
          </div>
          <div class="chat-time" id="chat-thinking-elapsed">${formatElapsed(Date.now() - thinkingStartedAt)}</div>
        </div>
        <div class="chat-content">
          <div class="thinking-row">
            <div class="thinking-main">
              <span class="thinking-phase-icon" aria-hidden="true">${phaseIcon}</span>
              <div class="thinking-dots" aria-hidden="true">
                <span></span><span></span><span></span>
              </div>
              <span class="thinking-label">${esc(phaseLabel)}</span>
            </div>
          </div>
        </div>
      `;

      list.appendChild(item);
      startThinkingTimer();

      requestAnimationFrame(() => {
        list.scrollTop = list.scrollHeight;
      });
    };

    const pushAssistantMessage = (payload) => {
      if (!payload) return;
      if (!currentState) currentState = {};
      if (!currentState.chat) currentState.chat = {};
      if (!Array.isArray(currentState.chat.messages)) currentState.chat.messages = [];

      currentState.chat.messages = [
        ...currentState.chat.messages,
        {
          id: payload.id || ("assistant:" + Date.now()),
          role: payload.role || "assistant",
          content: payload.content || "",
          createdAt: payload.createdAt || nowIso()
        }
      ];

      const activeTask = currentState.activeTask || {};
      if (payload.plan && (!activeTask.plan || !activeTask.plan.steps || !activeTask.plan.steps.length)) {
        currentState.activeTask = {
          ...activeTask,
          plan: payload.plan,
          status: activeTask.status || "planning",
          title: activeTask.title || "Task in progress"
        };
      }

      render(currentState);

      requestAnimationFrame(() => {
        const items = document.querySelectorAll(".chat-item.assistant:not(.thinking)");
        const last = items[items.length - 1];
        if (!last) return;
        const taskStatus = (currentState.activeTask || {}).status;
        if (taskStatus === "done") {
          last.classList.add("success-flash");
          last.addEventListener("animationend", () => last.classList.remove("success-flash"), { once: true });
        } else if (taskStatus === "failed") {
          last.classList.add("error-flash");
          last.addEventListener("animationend", () => last.classList.remove("error-flash"), { once: true });
        }
      });
    };

    const setActionButtonsDisabled = (visible) => {
      ["send-btn", "apply-btn", "revert-btn"].forEach((id) => {
        const button = byId(id);
        if (button) button.disabled = visible;
      });
    };

    const updateSendButton = (busy) => {
      const btn = byId("send-btn");
      if (!btn) return;

      btn.classList.toggle("stop-mode", busy);
      btn.disabled = false;
      btn.setAttribute("aria-label", busy ? "Stop generation" : "Send message");
      btn.setAttribute("title", busy ? "Stop generation" : "Send message");
      btn.innerHTML = `<span class="gp-send-icon" aria-hidden="true">${busy ? "■" : "➤"}</span>`;
    };

    const setBusy = (busy, text) => {
      busyCount = Math.max(0, busy ? busyCount + 1 : busyCount - 1);

      const visible = busyCount > 0;
      const overlay = byId("busy-overlay");
      const composeStatus = byId("compose-status");

      overlay.classList.toggle("visible", visible);
      overlay.setAttribute("aria-hidden", visible ? "false" : "true");
      document.body.classList.toggle("gp-is-busy", visible);

      if (text) byId("busy-text").textContent = text;
      composeStatus.textContent = visible ? (text || "Working…") : "Ready";

      const taskStatusDot = byId("task-status-dot");
      if (taskStatusDot) {
        taskStatusDot.className = visible ? "gp-status-dot is-working" : "gp-status-dot is-ready";
      }

      setLoadingIndicator(visible, text || getPhaseLabel());
      setActionButtonsDisabled(visible);
      updateSendButton(visible);
    };

    const clearBusy = () => {
      busyCount = 0;
      byId("busy-overlay").classList.remove("visible");
      byId("busy-overlay").setAttribute("aria-hidden", "true");
      document.body.classList.remove("gp-is-busy");
      byId("compose-status").textContent = "Ready";

      const taskStatusDot = byId("task-status-dot");
      if (taskStatusDot) taskStatusDot.className = "gp-status-dot is-ready";

      setLoadingIndicator(false);
      setActionButtonsDisabled(false);
      updateSendButton(false);
    };

    const renderError = (state) => {
      const banner = byId("error-banner");
      const copy = byId("error-copy");

      const notice = lastError || (
        state && state.ui && state.ui.notice
          ? { title: "Notice", message: state.ui.notice }
          : undefined
      );

      if (!notice) {
        banner.classList.add("hidden");
        banner.classList.remove("danger");
        copy.textContent = "";
        return;
      }

      banner.classList.remove("hidden");
      banner.classList.toggle("danger", Boolean(lastError));
      copy.textContent = String(notice.title || "Notice") + ": " + String(notice.message || "");
    };

    const renderHeader = (state) => {
      const provider = state.provider || {};
      const project = state.projectContextSummary || {};
      const workspace = state.workspace || {};
      const server = state.server || {};
      const task = state.activeTask || {};

      const repoName = project.repoName || (workspace.git && workspace.git.repoName) || workspace.folderName || "No repo";
      const branch = project.branch || (workspace.git && workspace.git.branch) || (state.session && state.session.branch) || "No branch";
      const providerName = provider.providerName || "Provider not set";
      const model = provider.model || "No model";
      const workflow = (state.workflow && state.workflow.selectedMode) || "auto";
      const connection = server.connected
        ? (provider.health === "error" ? "Degraded" : "Connected")
        : "Disconnected";

      byId("provider-line").textContent = providerName + " · " + connection + " · " + model;
      byId("repo-line").textContent = "Repo: " + repoName + " · Branch: " + branch;

      const connectionDot = byId("connection-dot");
      if (connectionDot) {
        const connectionClass = connection === "Connected"
          ? "is-connected"
          : connection === "Degraded"
            ? "is-degraded"
            : "is-disconnected";
        connectionDot.className = "gp-connection-dot " + connectionClass;
        connectionDot.setAttribute("aria-label", connection);
        connectionDot.setAttribute("title", connection);
      }

      // workflow-pill removed from UI for cleaner layout

      byId("connection-pill").textContent = connection;
      byId("connection-pill").className =
        connection === "Connected"
          ? "pill pill-success"
          : connection === "Degraded"
            ? "pill pill-warning"
            : "pill pill-danger";

      const taskActive = task.status && task.status !== "idle";
      byId("task-pill").textContent = taskActive ? statusLabel(task.status) : "Ready";
      byId("task-pill").className = taskActive ? "pill pill-accent" : "pill";

      // Sync execution mode selector with state
      const execMode = state.executionMode || "ask";
      document.querySelectorAll(".mode-btn").forEach((btn) => {
        const active = btn.dataset.mode === execMode;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-checked", String(active));
      });
    };

    const renderOverview = (state) => {
      const project = state.projectContextSummary || {};
      const recentFiles = (project.recentFiles || project.keyFiles || []).slice(0, 6);
      const recentSection = byId("idle-recent-section");

      if (!recentFiles.length) {
        recentSection.classList.add("hidden");
        byId("recent-files-list").innerHTML = "";
        return;
      }

      recentSection.classList.remove("hidden");
      byId("recent-files-list").innerHTML = recentFiles.map((file) => `
        <li class="row">
          <div class="item-head">
            <div>
              <div class="item-title">${esc(file)}</div>
              <div class="item-meta">Recently referenced in repository context</div>
            </div>
            <div class="item-actions">
              <button class="secondary" data-open-file="${esc(file)}" type="button">Open</button>
            </div>
          </div>
        </li>
      `).join("");
    };

    const renderSummary = (state) => {
      const task = state.activeTask || {};
      const steps = ((task.plan || {}).steps || []);
      const totalSteps = steps.length;
      const completedCount = steps.filter((step) =>
        ["applied", "ready", "done"].includes(step.status)
      ).length;
      const ratio = totalSteps > 0 ? Math.max(0, Math.min(1, completedCount / totalSteps)) : 0;
      const activeStepIndex = steps.findIndex((step) => !["applied", "ready", "done"].includes(step.status));
      const currentStep = totalSteps === 0 ? 0 : (activeStepIndex >= 0 ? activeStepIndex + 1 : totalSteps);

      // ── Only show the Task Status card when there is real progress
      // to display (plan steps with a progress ratio). During the initial
      // "planning" / "generating" phases without steps, the inline
      // thinking bubble in chat is the only status indicator — showing
      // the Task Status card too creates double-stacked UI that pushes
      // the chat down and overlaps visually.
      const hasProgress = totalSteps > 0;
      const isTerminalWithInfo = ["done", "failed", "ready_to_apply"].includes(task.status) && (task.title || task.summary);
      const showCard = hasProgress || isTerminalWithInfo;
      byId("assistant-summary-section").classList.toggle("hidden", !showCard);

      if (!showCard) return;

      byId("task-title").textContent = task.title || "";
      byId("task-status-pill").textContent = statusLabel(task.status);
      byId("task-status-pill").className = pillClassForStatus(task.status);
      const stepPill = byId("task-step-pill");
      stepPill.textContent = totalSteps > 0 ? ("Step " + Math.max(1, currentStep) + "/" + totalSteps) : "";
      stepPill.classList.toggle("hidden", totalSteps === 0);

      byId("task-summary").textContent =
        task.status === "ready_to_apply" ? "Changes ready to review."
        : task.status === "done" ? (task.summary || "Done.")
        : task.status === "failed" ? "See chat for details."
        : "";

      const progressSection = byId("task-progress-bar").parentElement.parentElement;
      if (totalSteps > 0) {
        progressSection.classList.remove("hidden");
        byId("task-progress-bar").style.width = Math.round(ratio * 100) + "%";
        byId("task-progress-label").textContent = completedCount + "/" + totalSteps + " steps";
      } else {
        progressSection.classList.add("hidden");
      }
    };

    const renderPlan = (state) => {
      const section = byId("plan-section");
      const list = byId("plan-list");
      const steps = (((state.activeTask || {}).plan || {}).steps || []);

      section.classList.toggle("hidden", !steps.length);

      list.innerHTML = steps.length
        ? steps.map((step, index) => {
            let cssClass = "pending";
            if (["applied", "ready", "done"].includes(step.status)) cssClass = "done";
            else if (step.status === "failed") cssClass = "failed";
            else if (index === 0 || step.status === "pending" || step.status === "planning") cssClass = "active";

            const marker =
              cssClass === "done" ? "✓" :
              cssClass === "failed" ? "!" :
              cssClass === "active" ? "→" : "•";

            return `
              <li class="row plan-item ${cssClass}">
                <div class="plan-marker">${marker}</div>
                <div>
                  <div class="item-title">${esc(step.title || step.action || ("Step " + (index + 1)))}</div>
                  <div class="item-meta">${esc(step.description || step.file || "Planned task step")}</div>
                </div>
              </li>
            `;
          }).join("")
        : "";

      // Show the "Approve & Execute" bar when plan steps exist and
      // the task has not started executing yet. Once executing/done,
      // hide it — the user already approved or the agent auto-ran.
      const approvalBar = byId("plan-approval-bar");
      if (approvalBar) {
        const taskStatus = ((state.activeTask) || {}).status || "idle";
        const planReady = steps.length > 0 && ["planning", "ready_to_apply"].includes(taskStatus);
        approvalBar.classList.toggle("hidden", !planReady);
      }
    };

    const renderScope = (state) => {
      const section = byId("scope-section");
      const list = byId("scope-list");
      const files = ((state.activeTask || {}).filesInScope || []);

      section.classList.toggle("hidden", !files.length);

      list.innerHTML = files.map((item) => `
        <li class="row">
          <div class="item-head">
            <div>
              <div class="item-title">${esc(item.path)}</div>
              <div class="item-meta">${esc(item.reason || "In scope for the current task")}</div>
            </div>
            <div class="item-actions">
              <button class="secondary" data-open-file="${esc(item.path)}" type="button">Open</button>
              <button class="ghost" data-reveal-file="${esc(item.path)}" type="button">Reveal</button>
            </div>
          </div>
        </li>
      `).join("");
    };

    const renderChanges = (state) => {
      const section = byId("changes-section");
      const list = byId("changes-list");
      const changes = ((state.activeTask || {}).changedFiles || []);

      section.classList.toggle("hidden", !changes.length);

      list.innerHTML = changes.map((item) => `
        <li class="row">
          <div class="item-head">
            <div>
              <div class="item-title">${esc((item.kind || "M") + " " + item.path)}</div>
              <div class="item-meta">${esc(item.summary || item.reason || item.status || "Proposed change")}</div>
            </div>
            <div class="item-actions">
              <span class="mini-tag">${esc(String(item.status || "pending"))}</span>
              <button class="secondary" data-open-file="${esc(item.path)}" type="button">Open</button>
              <button class="secondary" data-open-diff="${esc(item.path)}" type="button">Diff</button>
            </div>
          </div>
        </li>
      `).join("");
    };

    const roleAvatarLetter = (role) =>
      role === "user" ? "U" : role === "assistant" ? "G" : "S";

    const roleName = (role) =>
      role === "assistant" ? "GitPilot" : role === "user" ? "You" : "System";

    const injectCopyButtons = () => {
      document.querySelectorAll(".chat-content pre").forEach((pre) => {
        if (pre.querySelector(".code-copy-btn")) return;
        const btn = document.createElement("button");
        btn.className = "code-copy-btn";
        btn.textContent = "Copy";
        btn.type = "button";
        btn.addEventListener("click", () => {
          const code = pre.querySelector("code");
          const text = code ? code.textContent : pre.textContent;
          navigator.clipboard.writeText(text || "").then(() => {
            btn.textContent = "\u2713 Copied";
            btn.classList.add("copied");
            setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1800);
          });
        });
        pre.appendChild(btn);
      });
    };

    const renderChat = (state) => {
      const list = byId("chat-list");
      const emptyState = byId("chat-empty-state");
      const messages = getChatMessages(state);
      const thinkingVisible = busyCount > 0;
      const isStreaming = Boolean(streamingItemId);

      const hasRealMessages = (((state || {}).chat || {}).messages || []).length > 0;

      if (!hasRealMessages && !thinkingVisible && !isStreaming) {
        list.classList.add("hidden");
        list.innerHTML = "";
        if (emptyState) emptyState.classList.remove("hidden");
        return;
      }

      if (emptyState) emptyState.classList.add("hidden");
      list.classList.remove("hidden");

      // ── Key fix: preserve in-flight streaming and thinking DOM nodes.
      // The old code did `list.innerHTML = ...` which destroyed the
      // streaming <li> created by handleStreamChunk() and the thinking
      // bubble created by setLoadingIndicator(). Those nodes aren't in
      // state.chat.messages yet, so the rerender wiped them.
      //
      // New approach: detach transient nodes, rebuild the state-driven
      // messages, then re-attach the transient nodes at the end.
      const streamingNode = streamingItemId ? document.getElementById(streamingItemId) : null;
      const thinkingNode = byId("chat-thinking-item");

      // Detach transient nodes before innerHTML wipe
      if (streamingNode) streamingNode.remove();
      if (thinkingNode) thinkingNode.remove();

      list.innerHTML = messages.map((msg) => `
        <li class="chat-item ${esc(msg.role)}">
          <div class="chat-role-row">
            <div class="chat-role">
              <span class="role-avatar ${esc(msg.role)}">${roleAvatarLetter(msg.role)}</span>
              ${esc(roleName(msg.role))}
            </div>
            <div class="chat-time">${esc(formatTime(msg.createdAt))}</div>
          </div>
          <div class="chat-content">${msg.role === "assistant" ? normalizeAssistantText(msg.content) : esc(msg.content)}</div>
        </li>
      `).join("");

      // Re-attach transient nodes after the state-driven messages
      if (streamingNode) list.appendChild(streamingNode);

      if (thinkingVisible || isStreaming) {
        if (thinkingNode) {
          list.appendChild(thinkingNode);
        } else {
          setLoadingIndicator(true, getPhaseLabel());
        }
      }

      requestAnimationFrame(() => {
        list.scrollTop = list.scrollHeight;
        injectCopyButtons();
      });
    };

    const renderContextualActions = (state) => {
      const task = state.activeTask || {};
      const changes = task.changedFiles || [];
      const hasPatch = task.status === "ready_to_apply" || changes.length > 0;
      const showSecondaryRow = hasPatch;

      byId("secondary-actions-row").classList.toggle("hidden", !showSecondaryRow);
      byId("apply-btn").classList.toggle("hidden", !hasPatch);
      byId("revert-btn").classList.toggle("hidden", !hasPatch);

      const showTaskControls = taskIsActive(state) || hasPatch;
      byId("actions-section").classList.toggle("hidden", !showTaskControls);
    };

    const bindDynamicEvents = () => {
      if (dynamicHandlerAbortController) {
        dynamicHandlerAbortController.abort();
      }

      dynamicHandlerAbortController = new AbortController();
      const signal = dynamicHandlerAbortController.signal;

      document.querySelectorAll("[data-open-file]").forEach((node) => {
        node.addEventListener("click", () => {
          post({ type: "OPEN_CHANGED_FILE", payload: { path: node.getAttribute("data-open-file") } });
        }, { signal });
      });

      document.querySelectorAll("[data-open-diff]").forEach((node) => {
        node.addEventListener("click", () => {
          post({ type: "OPEN_CHANGED_DIFF", payload: { path: node.getAttribute("data-open-diff") } });
        }, { signal });
      });

      document.querySelectorAll("[data-reveal-file]").forEach((node) => {
        node.addEventListener("click", () => {
          post({ type: "REVEAL_FILE", payload: { path: node.getAttribute("data-reveal-file") } });
        }, { signal });
      });

      document.querySelectorAll("[data-quick-action]").forEach((node) => {
        node.addEventListener("click", () => {
          const action = node.getAttribute("data-quick-action");
          if (!action) return;
          setBusy(true, "Running " + action.replace(/_/g, " ") + "…");
          post({ type: "RUN_QUICK_ACTION", payload: { action } });
        }, { signal });
      });

      document.querySelectorAll("[data-suggestion]").forEach((node) => {
        node.addEventListener("click", () => {
          const text = node.getAttribute("data-suggestion");
          if (text) sendSuggestion(text);
        }, { signal });
      });
    };

    const createRenderSignature = (state) => JSON.stringify({
      provider: state && state.provider,
      server: state && state.server,
      workflow: state && state.workflow,
      projectContextSummary: state && state.projectContextSummary,
      activeTask: state && state.activeTask,
      chat: ((state || {}).chat || {}).messages || [],
      ui: state && state.ui,
      lastError,
      busyCount
    });

    const render = (state) => {
      const signature = createRenderSignature(state);
      if (signature === lastRenderedSignature) return;
      lastRenderedSignature = signature;

      currentState = state || {};

      renderHeader(currentState);
      renderError(currentState);
      renderOverview(currentState);
      renderSummary(currentState);
      renderPlan(currentState);
      renderScope(currentState);
      renderChanges(currentState);
      renderChat(currentState);
      renderContextualActions(currentState);
      bindDynamicEvents();
    };

    const sendChat = () => {
      const btn = byId("send-btn");

      if (btn && btn.classList.contains("stop-mode")) {
        post({ type: "CANCEL_TASK" });
        clearBusy();
        return;
      }

      const input = byId("chat-input");
      const text = input.value.trim();
      if (!text) return;

      input.value = "";
      input.style.height = "auto";
      pushOptimisticUserMessage(text);
      setBusy(true, getPhaseLabel());
      post({ type: "SEND_CHAT", payload: { text } });
    };

    const sendSuggestion = (text) => {
      if (!text) return;
      const input = byId("chat-input");
      if (input) {
        input.value = "";
        input.style.height = "auto";
      }
      pushOptimisticUserMessage(text);
      setBusy(true, getPhaseLabel());
      post({ type: "SEND_CHAT", payload: { text } });
    };

    const requestRefreshContext = () => {
      setBusy(true, "Refreshing repository context…");
      post({ type: "REFRESH_PROJECT_CONTEXT" });
    };

    const handleContextualAction = () => {
      const action = byId("contextual-btn").dataset.action;

      if (action === "apply") {
        setBusy(true, "Applying proposed changes…");
        post({ type: "APPLY_PROPOSED_CHANGES" });
        return;
      }

      if (action === "retry") {
        const messages = getChatMessages(currentState);
        const latestUser = [...messages].reverse().find((msg) => msg.role === "user");
        if (!latestUser) {
          requestRefreshContext();
          return;
        }
        setBusy(true, "Retrying request…");
        post({ type: "SEND_CHAT", payload: { text: latestUser.content } });
        return;
      }

      requestRefreshContext();
    };

    // ── V2 event handlers ──

    const handleToolActivity = (payload) => {
      const feed = byId("tool-activity-feed");
      const list = byId("activity-list");
      if (!feed || !list) return;

      feed.classList.remove("hidden");

      let item = document.getElementById("activity-" + payload.id);
      if (!item) {
        item = document.createElement("li");
        item.id = "activity-" + payload.id;
        item.className = "activity-item running";
        item.innerHTML = `
          <span class="activity-icon">&#9679;</span>
          <span class="activity-name">${esc(payload.name)}</span>
          <span class="activity-status">running</span>
        `;
        list.appendChild(item);
        if (list.children.length > 8) list.removeChild(list.firstChild);
      }

      item.className = "activity-item " + esc(payload.status);
      const statusEl = item.querySelector(".activity-status");
      if (statusEl) statusEl.textContent = payload.status;

      const iconEl = item.querySelector(".activity-icon");
      if (iconEl) {
        iconEl.innerHTML = payload.status === "completed" ? "&#10003;"
          : payload.status === "failed" ? "&#10007;" : "&#9679;";
      }
    };

    let pendingApprovalId = null;

    const showApprovalCard = (payload) => {
      const card = byId("approval-card");
      if (!card) return;

      pendingApprovalId = payload.id;
      byId("approval-title").textContent = "GitPilot wants to " + String(payload.tool || "use a tool").replace(/_/g, " ");
      byId("approval-summary").textContent = payload.summary || "";

      const riskEl = byId("approval-risk");
      riskEl.textContent = payload.riskLevel || "medium";
      riskEl.className = "pill approval-risk risk-" + (payload.riskLevel || "medium");

      const diffEl = byId("approval-diff");
      if (payload.diffPreview) {
        diffEl.textContent = payload.diffPreview;
        diffEl.classList.remove("hidden");
      } else {
        diffEl.classList.add("hidden");
      }

      card.classList.remove("hidden");
    };

    const resolveApproval = (approved, scope) => {
      if (!pendingApprovalId) return;
      post({
        type: "TOOL_APPROVAL_RESPONSE",
        payload: { id: pendingApprovalId, approved, scope: scope || "once" },
      });
      pendingApprovalId = null;
      const card = byId("approval-card");
      if (card) card.classList.add("hidden");
    };

    const handlePlanStepUpdate = (payload) => {
      const list = byId("plan-list");
      if (!list) return;

      const items = list.querySelectorAll(".plan-item");
      const item = items[payload.stepIndex];
      if (!item) return;

      const marker = item.querySelector(".plan-marker");
      if (!marker) return;

      if (payload.status === "started" || payload.status === "running" || payload.status === "pending") {
        item.className = "row plan-item active";
        marker.textContent = "\u2192";
      } else if (payload.status === "completed" || payload.status === "applied") {
        item.className = "row plan-item done";
        marker.textContent = "\u2713";
      } else if (payload.status === "failed") {
        item.className = "row plan-item failed";
        marker.textContent = "!";
      }
    };

    const handleTerminalOutput = (payload) => {
      const panel = byId("terminal-panel");
      const output = byId("terminal-output");
      if (!panel || !output) return;

      panel.classList.remove("hidden");
      output.textContent += payload.text || "";

      requestAnimationFrame(() => {
        output.scrollTop = output.scrollHeight;
      });
    };

    const handleDiagnostics = (payload) => {
      const bar = byId("results-bar");
      if (!bar) return;
      bar.classList.remove("hidden");

      const errBadge = byId("diag-errors-badge");
      const warnBadge = byId("diag-warnings-badge");

      if (payload.errors > 0 && errBadge) {
        errBadge.textContent = payload.errors + " error" + (payload.errors !== 1 ? "s" : "");
        errBadge.classList.remove("hidden");
      }
      if (payload.warnings > 0 && warnBadge) {
        warnBadge.textContent = payload.warnings + " warning" + (payload.warnings !== 1 ? "s" : "");
        warnBadge.classList.remove("hidden");
      }
    };

    const handleTestResult = (payload) => {
      const bar = byId("results-bar");
      if (!bar) return;
      bar.classList.remove("hidden");

      const passed = byId("test-passed-badge");
      const failed = byId("test-failed-badge");
      const skipped = byId("test-skipped-badge");

      if (payload.passed > 0 && passed) {
        passed.textContent = payload.passed + " passed";
        passed.classList.remove("hidden");
      }
      if (payload.failed > 0 && failed) {
        failed.textContent = payload.failed + " failed";
        failed.classList.remove("hidden");
      }
      if (payload.skipped > 0 && skipped) {
        skipped.textContent = payload.skipped + " skipped";
        skipped.classList.remove("hidden");
      }
    };

    const bindStaticEvents = () => {
      byId("send-btn").addEventListener("click", sendChat);

      byId("chat-input").addEventListener("input", () => {
        const input = byId("chat-input");
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 200) + "px";
      });

      byId("chat-input").addEventListener("keydown", (event) => {
        if ((event.key === "Enter" && !event.shiftKey) || ((event.ctrlKey || event.metaKey) && event.key === "Enter")) {
          event.preventDefault();
          sendChat();
        }
        if (event.key === "Escape") {
          event.preventDefault();
          post({ type: "CANCEL_TASK" });
          clearBusy();
        }
      });

      byId("setup-context-btn").addEventListener("click", requestRefreshContext);
      byId("refresh-context-card").addEventListener("click", requestRefreshContext);

      byId("provider-setup-btn").addEventListener("click", () => {
        post({ type: "OPEN_PROVIDER_SETUP" });
      });

      byId("idle-setup-btn").addEventListener("click", () => {
        post({ type: "OPEN_SETUP_WIZARD" });
      });

      // ── New Chat button: clears chat and starts a fresh session ──
      byId("open-workspace-btn")?.addEventListener("click", () => {
        post({ type: "OPEN_WORKSPACE" });
      });

      byId("new-chat-btn").addEventListener("click", () => {
        // Clear local webview state immediately for instant feedback
        const list = byId("chat-list");
        if (list) list.innerHTML = "";
        const emptyState = byId("chat-empty-state");
        if (emptyState) emptyState.classList.remove("hidden");
        streamingItemId = null;
        clearBusy();
        // Tell the extension host to create a fresh session
        post({ type: "NEW_SESSION" });
      });

      // ── Plan approval: user must click before execution proceeds ──
      byId("plan-approve-btn").addEventListener("click", () => {
        byId("plan-approval-bar").classList.add("hidden");
        setBusy(true, "Executing approved plan…");
        post({ type: "APPROVE_PLAN" });
      });

      byId("plan-reject-btn").addEventListener("click", () => {
        byId("plan-approval-bar").classList.add("hidden");
        clearBusy();
        post({ type: "REJECT_PLAN" });
      });

      byId("apply-btn").addEventListener("click", () => {
        setBusy(true, "Applying proposed changes…");
        post({ type: "APPLY_PROPOSED_CHANGES" });
      });

      byId("revert-btn").addEventListener("click", () => {
        setBusy(true, "Reverting proposed changes…");
        post({ type: "REVERT_PROPOSED_CHANGES" });
      });

      byId("replan-btn").addEventListener("click", () => {
        setBusy(true, "Regenerating plan…");
        post({ type: "REGENERATE_TASK_PLAN" });
      });

      byId("setup-btn").addEventListener("click", () => {
        post({ type: "OPEN_SETUP_WIZARD" });
      });

      byId("settings-header-btn").addEventListener("click", () => {
        post({ type: "OPEN_SETTINGS" });
      });

      // V2 approval card buttons
      byId("approval-allow")?.addEventListener("click", () => resolveApproval(true, "once"));
      byId("approval-allow-session")?.addEventListener("click", () => resolveApproval(true, "session"));
      byId("approval-deny")?.addEventListener("click", () => resolveApproval(false));

      // Terminal close
      byId("terminal-close")?.addEventListener("click", () => {
        const panel = byId("terminal-panel");
        if (panel) panel.classList.add("hidden");
      });

      // ── Execution mode selector ──
      document.querySelectorAll(".mode-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          document.querySelectorAll(".mode-btn").forEach((b) => {
            b.classList.remove("active");
            b.setAttribute("aria-checked", "false");
          });
          btn.classList.add("active");
          btn.setAttribute("aria-checked", "true");
          post({ type: "SET_EXECUTION_MODE", payload: { mode: btn.dataset.mode } });
        });
      });
    };

    let streamingItemId = null;

    const handleStreamChunk = (payload) => {
      if (!payload || !payload.content) return;
      const list = byId("chat-list");
      const emptyState = byId("chat-empty-state");
      if (emptyState) emptyState.classList.add("hidden");
      list.classList.remove("hidden");

      const existing = byId("chat-thinking-item");
      if (existing) { existing.remove(); stopThinkingTimer(); }

      let item = streamingItemId ? document.getElementById(streamingItemId) : null;

      if (!item) {
        streamingItemId = "streaming-" + Date.now();
        item = document.createElement("li");
        item.id = streamingItemId;
        item.className = "chat-item assistant streaming";
        item.innerHTML = `
          <div class="chat-role-row">
            <div class="chat-role">
              <span class="role-avatar assistant">G</span>
              GitPilot
            </div>
            <div class="chat-time">${esc(formatTime(nowIso()))}</div>
          </div>
          <div class="chat-content"></div>
        `;
        list.appendChild(item);
      }

      const content = item.querySelector(".chat-content");
      if (content) {
        content.innerHTML = normalizeAssistantText(
          (content.getAttribute("data-raw") || "") + payload.content
        );
        content.setAttribute("data-raw",
          (content.getAttribute("data-raw") || "") + payload.content
        );
      }

      requestAnimationFrame(() => {
        list.scrollTop = list.scrollHeight;
        injectCopyButtons();
      });
    };

    const finalizeStream = (payload) => {
      const item = streamingItemId ? document.getElementById(streamingItemId) : null;
      if (item) {
        item.classList.remove("streaming");
        item.classList.add("success-flash");
        item.addEventListener("animationend", () => item.classList.remove("success-flash"), { once: true });
      }

      if (item) {
        const rawText = item.querySelector(".chat-content")?.getAttribute("data-raw") || "";
        if (!currentState) currentState = {};
        if (!currentState.chat) currentState.chat = {};
        if (!Array.isArray(currentState.chat.messages)) currentState.chat.messages = [];
        currentState.chat.messages.push({
          id: (payload && payload.id) || streamingItemId,
          role: "assistant",
          content: rawText,
          createdAt: (payload && payload.createdAt) || nowIso()
        });
      }

      streamingItemId = null;
      clearBusy();
    };

    window.addEventListener("message", (event) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;

      if (msg.type === "STATE_SYNC") {
        lastError = undefined;
        const syncState = msg.payload || {};
        const taskStatus = ((syncState.activeTask) || {}).status || "idle";

        const isTerminal = ["idle", "done", "failed", "ready_to_apply"].includes(taskStatus);
        const isActive = ["planning", "generating", "reviewing"].includes(taskStatus);

        if (isTerminal) {
          clearBusy();
        } else if (isActive && busyCount === 0) {
          // The v2 stream may have cleared busy (via CHAT_STREAM_END or
          // a premature "done" status_change) but the extension host is
          // still working (e.g. batch fallback). Re-activate the thinking
          // bubble so the user sees the animation during the entire call.
          setBusy(true, getPhaseLabel());
        }

        render(syncState);
        return;
      }

      if (msg.type === "CHAT_RESPONSE") {
        clearBusy();
        pushAssistantMessage(msg.payload);
        return;
      }

      if (msg.type === "CHAT_STREAM_CHUNK") {
        handleStreamChunk(msg.payload);
        return;
      }

      if (msg.type === "CHAT_STREAM_END") {
        finalizeStream(msg.payload);
        return;
      }

      if (msg.type === "AGENT_TOOL_ACTIVITY") {
        handleToolActivity(msg.payload);
        return;
      }

      if (msg.type === "TOOL_APPROVAL_REQUEST") {
        showApprovalCard(msg.payload);
        return;
      }

      if (msg.type === "PLAN_STEP_UPDATE") {
        handlePlanStepUpdate(msg.payload);
        return;
      }

      if (msg.type === "TERMINAL_OUTPUT") {
        handleTerminalOutput(msg.payload);
        return;
      }

      if (msg.type === "DIAGNOSTICS_RESULT") {
        handleDiagnostics(msg.payload);
        return;
      }

      if (msg.type === "TEST_RESULT") {
        handleTestResult(msg.payload);
        return;
      }

      if (msg.type === "ERROR") {
        clearBusy();
        lastError = msg.payload || { title: "Error", message: "Unknown error" };
        render(currentState || {});
      }
    });

    const initialStatusDot = byId("task-status-dot");
    if (initialStatusDot) initialStatusDot.className = "gp-status-dot is-ready";

    bindStaticEvents();
    post({ type: "INIT" });
