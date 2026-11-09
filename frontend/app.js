const STORAGE_KEY = "deepagents.patient-chat.current.v2";
    const LEGACY_STORAGE_KEY = "deepagents.patient-chat.v1";
    const conversation = document.querySelector("#conversation");
    const sessionList = document.querySelector("#sessionList");
    const form = document.querySelector("#chatForm");
    const input = document.querySelector("#messageInput");
    const sendButton = document.querySelector("#sendButton");
    const stopButton = document.querySelector("#stopButton");
    const patientId = document.querySelector("#patientId");
    const welcome = document.querySelector("#welcome");
    const agentProgress = document.querySelector("#agentProgress");
    const agentStatus = document.querySelector("#agentStatus");
    const agentTodos = document.querySelector("#agentTodos");
    let state = readState();
    let activeController = null;
    let sessionCatalog = [];

    function newId(prefix) {
      return `${prefix}-${crypto.randomUUID()}`;
    }

    function readState() {
      try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
        if (saved && typeof saved.threadId === "string") return { ...saved, messages: [], pendingInterrupts: [] };
        const legacy = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || "null");
        if (legacy && typeof legacy.threadId === "string") return { ...legacy, messages: [], pendingInterrupts: [] };
      } catch (error) {
        console.warn("读取本地聊天记录失败", error);
      }
      return { threadId: newId("patient-chat"), messages: [], patientId: "demo-patient-0001" };
    }

    function saveState() {
      state.patientId = patientId.value.trim() || "demo-patient-0001";
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ threadId: state.threadId, patientId: state.patientId }));
    }

    function renderAgentState(snapshot) {
      if (!snapshot || typeof snapshot !== "object") return;
      agentProgress.hidden = false;
      agentStatus.textContent = snapshot.activity || "正在处理";
      agentTodos.replaceChildren();
      for (const todo of Array.isArray(snapshot.todos) ? snapshot.todos : []) {
        if (!todo || typeof todo.content !== "string") continue;
        const item = document.createElement("li");
        item.textContent = todo.content;
        item.dataset.status = ["pending", "in_progress", "completed"].includes(todo.status) ? todo.status : "pending";
        agentTodos.append(item);
      }
      agentTodos.hidden = agentTodos.childElementCount === 0;
    }

    function clearAgentState() {
      agentProgress.hidden = true;
      agentStatus.textContent = "";
      agentTodos.replaceChildren();
    }

    function addMessage(role, content) {
      const item = { id: newId("msg"), role, content };
      state.messages.push(item);
      saveState();
      return item;
    }

    function renderHistory() {
      welcome.hidden = state.messages.length > 0;
      conversation.querySelectorAll(".message").forEach(node => node.remove());
      state.messages.forEach((message, index) => {
        const rendered = renderMessage(message);
        if (index === state.messages.length - 1 && message.role === "assistant" && state.pendingInterrupts?.length) {
          showInterrupt(rendered.bubble, state.pendingInterrupts);
        }
      });
      scrollToBottom();
    }

    function renderSessionCatalog() {
      sessionList.replaceChildren();
      if (!sessionCatalog.length) {
        const empty = document.createElement("p");
        empty.className = "session-item-meta";
        empty.textContent = "还没有保存的会话";
        sessionList.append(empty);
        return;
      }
      for (const session of sessionCatalog) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "session-item";
        button.setAttribute("aria-current", String(session.thread_id === state.threadId));
        const title = document.createElement("span");
        title.className = "session-item-title";
        title.textContent = session.title || "新会话";
        const meta = document.createElement("span");
        meta.className = "session-item-meta";
        meta.textContent = `${session.status} · ${new Date(session.updated_at).toLocaleString()}`;
        button.append(title, meta);
        button.addEventListener("click", () => loadSession(session.thread_id));
        sessionList.append(button);
      }
    }

    async function refreshSessionCatalog() {
      const id = encodeURIComponent(patientId.value.trim() || "demo-patient-0001");
      const response = await fetch(`/api/v1/agents/patient/sessions?patientId=${id}`);
      if (!response.ok) throw new Error("无法读取服务端会话列表");
      sessionCatalog = (await response.json()).sessions || [];
      renderSessionCatalog();
    }

    async function loadSession(threadId) {
      if (activeController) activeController.abort();
      const id = encodeURIComponent(patientId.value.trim() || "demo-patient-0001");
      const response = await fetch(`/api/v1/agents/patient/sessions/${encodeURIComponent(threadId)}?patientId=${id}`);
      if (!response.ok) throw new Error("无法加载这个会话");
      const saved = (await response.json()).session;
      state = {
        threadId: saved.thread_id,
        patientId: saved.patient_id,
        messages: saved.messages || [],
        agentState: saved.agent_state || {},
        pendingInterrupts: saved.pending_interrupts || [],
      };
      patientId.value = state.patientId;
      saveState();
      if (state.agentState && Object.keys(state.agentState).length) renderAgentState(state.agentState);
      else clearAgentState();
      renderHistory();
      renderSessionCatalog();
    }

    async function initializeSessions() {
      const legacy = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || "null");
      await refreshSessionCatalog();
      if (legacy?.threadId && Array.isArray(legacy.messages) && legacy.messages.length &&
          !sessionCatalog.some(item => item.thread_id === legacy.threadId)) {
        const imported = await fetch("/api/v1/agents/patient/sessions/import", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threadId: legacy.threadId, patientId: legacy.patientId || "demo-patient-0001", messages: legacy.messages }),
        });
        if (!imported.ok) throw new Error("旧会话迁入服务端失败");
        localStorage.removeItem(LEGACY_STORAGE_KEY);
        await refreshSessionCatalog();
      }
      const target = sessionCatalog.find(item => item.thread_id === state.threadId) || sessionCatalog[0];
      if (target) await loadSession(target.thread_id);
    }

    function renderMessage(message, pending = false, isError = false) {
      welcome.hidden = true;
      const row = document.createElement("div");
      row.className = `message ${message.role}${isError ? " message-error" : ""}`;
      const bubble = document.createElement("div");
      bubble.className = `bubble${pending ? " pending" : ""}`;
      if (message.role === "assistant") renderMarkdown(bubble, message.content);
      else bubble.textContent = message.content;
      if (message.role === "assistant") {
        const avatar = document.createElement("div");
        avatar.className = "avatar";
        avatar.textContent = "✳";
        row.append(avatar, bubble);
      } else {
        row.append(bubble);
      }
      conversation.append(row);
      scrollToBottom();
      return { row, bubble };
    }

    function renderMarkdown(target, source) {
      target.replaceChildren();
      const lines = source.replace(/\r/g, "").split("\n");
      let index = 0;
      while (index < lines.length) {
        if (!lines[index].trim()) { index += 1; continue; }

        const fence = lines[index].match(/^\s*```([\w-]*)\s*$/);
        if (fence) {
          index += 1;
          const code = [];
          while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) code.push(lines[index++]);
          if (index < lines.length) index += 1;
          const pre = document.createElement("pre");
          const codeNode = document.createElement("code");
          if (fence[1]) codeNode.className = `language-${fence[1]}`;
          codeNode.textContent = code.join("\n");
          pre.append(codeNode);
          target.append(pre);
          continue;
        }

        const heading = lines[index].match(/^(#{1,6})\s+(.+)$/);
        if (heading) {
          const node = document.createElement(`h${heading[1].length}`);
          appendInlineMarkdown(node, heading[2]);
          target.append(node);
          index += 1;
          continue;
        }

        if (/^\s*([-*_])\s*\1\s*\1[-*_\s]*$/.test(lines[index])) {
          target.append(document.createElement("hr"));
          index += 1;
          continue;
        }

        if (/^\s*>/.test(lines[index])) {
          const quote = document.createElement("blockquote");
          while (index < lines.length && /^\s*>/.test(lines[index])) {
            const line = lines[index++].replace(/^\s*>\s?/, "");
            if (quote.childNodes.length) quote.append(document.createElement("br"));
            appendInlineMarkdown(quote, line);
          }
          target.append(quote);
          continue;
        }

        const listStart = lines[index].match(/^\s*((?:[-+*])|(?:\d+\.))\s+(.+)$/);
        if (listStart) {
          const ordered = /^\d/.test(listStart[1]);
          const list = document.createElement(ordered ? "ol" : "ul");
          while (index < lines.length) {
            const item = lines[index].match(/^\s*((?:[-+*])|(?:\d+\.))\s+(.+)$/);
            if (!item || /^\d/.test(item[1]) !== ordered) break;
            const li = document.createElement("li");
            appendInlineMarkdown(li, item[2]);
            list.append(li);
            index += 1;
          }
          target.append(list);
          continue;
        }

        const paragraph = [];
        while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines[index])) {
          paragraph.push(lines[index++].trim());
        }
        if (!paragraph.length) paragraph.push(lines[index++]);
        const p = document.createElement("p");
        appendInlineMarkdown(p, paragraph.join(" "));
        target.append(p);
      }
    }

    function startsMarkdownBlock(line) {
      return /^\s*```|^#{1,6}\s|^\s*>|^\s*(([-+*])|\d+\.)\s+/.test(line);
    }

    function appendInlineMarkdown(parent, text) {
      const tokenPattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]\n]+\]\([^)]+\))/g;
      let cursor = 0;
      for (const match of text.matchAll(tokenPattern)) {
        parent.append(document.createTextNode(text.slice(cursor, match.index)));
        const token = match[0];
        let node;
        if (token.startsWith("`")) {
          node = document.createElement("code");
          node.textContent = token.slice(1, -1);
        } else if (token.startsWith("**") || token.startsWith("__")) {
          node = document.createElement("strong");
          node.textContent = token.slice(2, -2);
        } else if (token.startsWith("*") || token.startsWith("_")) {
          node = document.createElement("em");
          node.textContent = token.slice(1, -1);
        } else {
          const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
          const url = link && safeMarkdownUrl(link[2]);
          if (url) {
            node = document.createElement("a");
            node.href = url;
            node.target = "_blank";
            node.rel = "noopener noreferrer";
            node.textContent = link[1];
          } else {
            node = document.createTextNode(link ? link[1] : token);
          }
        }
        parent.append(node);
        cursor = match.index + token.length;
      }
      parent.append(document.createTextNode(text.slice(cursor)));
    }

    function safeMarkdownUrl(value) {
      try {
        const url = new URL(value, window.location.href);
        return ["https:", "http:", "mailto:"].includes(url.protocol) ? url.href : null;
      } catch {
        return null;
      }
    }

    function scrollToBottom() {
      conversation.scrollTop = conversation.scrollHeight;
    }

    function setBusy(busy) {
      sendButton.disabled = busy;
      input.disabled = busy;
      stopButton.hidden = !busy;
      if (!busy) input.focus();
    }

    function showInterrupt(bubble, interrupts) {
      const panel = document.createElement("div");
      panel.className = "interrupt";
      const text = document.createElement("p");
      text.textContent = "助手请求确认一项写入操作。请看清操作内容，再选择是否继续。";
      panel.append(text);
      const actions = document.createElement("div");
      actions.className = "interrupt-actions";
      for (const [label, approved] of [["确认执行", true], ["暂不执行", false]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        if (approved) button.className = "approve";
        button.addEventListener("click", () => {
          actions.querySelectorAll("button").forEach(action => { action.disabled = true; });
          resumeWithDecision(interrupts, approved);
        });
        actions.append(button);
      }
      panel.append(actions);
      bubble.append(panel);
    }

    async function resumeWithDecision(interrupts, approved) {
      const resume = interrupts.map(item => ({
        interruptId: item.id,
        status: "resolved",
        payload: { approved },
      }));
      await runAgent({ resume });
    }

    async function runAgent({ resume } = {}) {
      if (activeController) return;
      const runState = state;
      const lastUser = [...state.messages].reverse().find(message => message.role === "user");
      if (!lastUser) return;
      const assistant = addMessage("assistant", "");
      const rendered = renderMessage(assistant, true);
      let pendingInterrupts = [];
      const controller = new AbortController();
      activeController = controller;
      setBusy(true);
      let streamError = null;
      try {
        const body = {
          threadId: runState.threadId,
          runId: newId("run"),
          messages: runState.messages
            .filter(message => (message.role === "user" || message.role === "assistant") &&
              (message.role !== "assistant" || message.content.trim()))
            .map(({ id, role, content }) => ({ id, role, content })),
          tools: [],
          context: [{ description: "patient_id", value: patientId.value.trim() || "demo-patient-0001" }],
          forwardedProps: {},
        };
        if (resume) body.resume = resume;
        const response = await fetch("/api/v1/agents/patient/ag-ui", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.error || `请求失败（${response.status}）`);
        }
        if (!response.body) throw new Error("浏览器没有收到 SSE 响应流");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finished = false;
        while (!finished) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
          const blocks = buffer.split(/\r?\n\r?\n/);
          buffer = blocks.pop() || "";
          for (const block of blocks) {
            const data = block.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trim()).join("\n");
            if (!data) continue;
            let event;
            try { event = JSON.parse(data); } catch { continue; }
            if (event.type === "STATE_SNAPSHOT") {
              renderAgentState(event.snapshot);
            } else if (event.type === "TEXT_MESSAGE_CONTENT") {
              assistant.content += event.delta || "";
              renderMarkdown(rendered.bubble, assistant.content);
              rendered.bubble.classList.add("pending");
              scrollToBottom();
            } else if (event.type === "RUN_FINISHED") {
              finished = true;
              rendered.bubble.classList.remove("pending");
              const outcome = event.outcome;
              if (outcome?.type === "interrupt" && Array.isArray(outcome.interrupts)) {
                pendingInterrupts = outcome.interrupts;
              }
            } else if (event.type === "RUN_ERROR") {
              throw new Error(event.message || "Agent 本轮运行失败");
            }
          }
          if (done) break;
        }
        if (!assistant.content) assistant.content = "本轮已结束。你可以继续发送消息。";
      } catch (error) {
        if (error.name === "AbortError") {
          assistant.content = assistant.content ? `${assistant.content}\n\n（已停止生成）` : "已停止生成。你可以继续发送消息。";
        } else {
          streamError = error;
          assistant.content = `${assistant.content}${assistant.content ? "\n\n" : ""}连接出错：${error.message}`;
          rendered.row.classList.add("message-error");
        }
      } finally {
        runState.pendingInterrupts = pendingInterrupts;
        renderMarkdown(rendered.bubble, assistant.content);
        if (pendingInterrupts.length) showInterrupt(rendered.bubble, pendingInterrupts);
        rendered.bubble.classList.remove("pending");
        if (state === runState) saveState();
        refreshSessionCatalog().catch(error => console.warn("刷新会话列表失败", error));
        if (activeController === controller) activeController = null;
        setBusy(false);
      }
      if (streamError) console.error("Agent SSE 请求失败", streamError);
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      const content = input.value.trim();
      if (!content || activeController) return;
      addMessage("user", content);
      input.value = "";
      renderMessage(state.messages.at(-1));
      runAgent();
    });

    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    stopButton.addEventListener("click", () => activeController?.abort());
    document.querySelector("#newChat").addEventListener("click", () => {
      activeController?.abort();
      state = { threadId: newId("patient-chat"), messages: [], patientId: patientId.value.trim() || "demo-patient-0001" };
      clearAgentState();
      saveState();
      renderHistory();
      renderSessionCatalog();
      input.focus();
    });

    patientId.value = state.patientId || "demo-patient-0001";
    patientId.addEventListener("change", saveState);
    document.querySelectorAll(".suggestion").forEach(button => {
      button.addEventListener("click", () => {
        input.value = button.textContent;
        form.requestSubmit();
      });
    });
    renderHistory();
    initializeSessions().catch(error => console.error("加载服务端会话失败", error));

    fetch("/health").then(response => {
      if (!response.ok) throw new Error("offline");
      document.querySelector("#healthDot").classList.add("online");
      document.querySelector("#healthText").textContent = "服务已连接";
    }).catch(() => {
      document.querySelector("#healthText").textContent = "服务未连接";
    });
