const STORAGE_KEY = "openclaw.patient-chat.v1";
    const conversation = document.querySelector("#conversation");
    const form = document.querySelector("#chatForm");
    const input = document.querySelector("#messageInput");
    const sendButton = document.querySelector("#sendButton");
    const stopButton = document.querySelector("#stopButton");
    const patientId = document.querySelector("#patientId");
    const welcome = document.querySelector("#welcome");
    let state = readState();
    let activeController = null;

    function newId(prefix) {
      return `${prefix}-${crypto.randomUUID()}`;
    }

    function readState() {
      try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
        if (saved && Array.isArray(saved.messages) && typeof saved.threadId === "string") return saved;
      } catch (error) {
        console.warn("读取本地聊天记录失败", error);
      }
      return { threadId: newId("patient-chat"), messages: [], patientId: "demo-patient-0001" };
    }

    function saveState() {
      state.patientId = patientId.value.trim() || "demo-patient-0001";
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
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
      for (const message of state.messages) renderMessage(message);
      scrollToBottom();
    }

    function renderMessage(message, pending = false, isError = false) {
      welcome.hidden = true;
      const row = document.createElement("div");
      row.className = `message ${message.role}${isError ? " message-error" : ""}`;
      const bubble = document.createElement("div");
      bubble.className = `bubble${pending ? " pending" : ""}`;
      bubble.textContent = message.content;
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
      const controller = new AbortController();
      activeController = controller;
      setBusy(true);
      let streamError = null;
      try {
        const body = {
          threadId: runState.threadId,
          runId: newId("run"),
          messages: runState.messages.map(({ id, role, content }) => ({ id, role, content })),
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
            if (event.type === "TEXT_MESSAGE_CONTENT") {
              assistant.content += event.delta || "";
              rendered.bubble.textContent = assistant.content;
              rendered.bubble.classList.add("pending");
              scrollToBottom();
            } else if (event.type === "RUN_FINISHED") {
              finished = true;
              rendered.bubble.classList.remove("pending");
              const outcome = event.outcome;
              if (outcome?.type === "interrupt" && Array.isArray(outcome.interrupts)) {
                showInterrupt(rendered.bubble, outcome.interrupts);
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
        rendered.bubble.textContent = assistant.content;
        rendered.bubble.classList.remove("pending");
        if (state === runState) saveState();
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
      saveState();
      renderHistory();
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

    fetch("/health").then(response => {
      if (!response.ok) throw new Error("offline");
      document.querySelector("#healthDot").classList.add("online");
      document.querySelector("#healthText").textContent = "服务已连接";
    }).catch(() => {
      document.querySelector("#healthText").textContent = "服务未连接";
    });
