    const conversation = document.querySelector("#conversation");
    const sessionList = document.querySelector("#sessionList");
    const form = document.querySelector("#chatForm");
    const input = document.querySelector("#messageInput");
    const sendButton = document.querySelector("#sendButton");
    const stopButton = document.querySelector("#stopButton");
    const patientId = document.querySelector("#patientId");
    const welcome = document.querySelector("#welcome");
    const modelSelect = document.querySelector("#modelSelect");
    const modelContext = document.querySelector("#modelContext");
    const profileDialog = document.querySelector("#profileDialog");
    const profilePanel = document.querySelector("#profilePanel");
    const profileTabs = document.querySelector("#profileTabs");
    const fileInput = document.querySelector("#fileInput");
    const uploadButton = document.querySelector("#uploadButton");
    const uploadStatus = document.querySelector("#uploadStatus");
    let state = { threadId: newId("patient-chat"), messages: [], patientId: "demo-patient-0001" };
    let pendingUploads = [];
    let uploadingFiles = false;
    let activeController = null;
    let sessionCatalog = [];
    let profileData = null;
    let activeProfileTab = "fhir";
    let availableModels = [];

    function formatTokens(value) {
      const tokens = Number(value) || 0;
      if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(2)}M`;
      if (tokens >= 10_000) return `${Math.round(tokens / 1_000)}K`;
      return tokens.toLocaleString();
    }

    function updateModelContext(model) {
      if (!model) return;
      const windowTokens = Number(model.context_window || model.contextWindow || 0);
      const inputBudget = Number(model.input_budget_tokens || model.inputBudgetTokens || Math.max(0, windowTokens - (model.max_output_tokens || model.maxOutputTokens || 0)));
      const maxOutput = Number(model.max_output_tokens || model.maxOutputTokens || 0);
      modelContext.textContent = `${model.name || model.display_name || model.id || model.model_id} · 总窗口 ${formatTokens(windowTokens)} · 输入预算 ${formatTokens(inputBudget)} · 最大输出 ${formatTokens(maxOutput)}`;
    }

    async function loadModels() {
      const response = await fetch("/api/v1/agents/patient/models");
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "无法读取模型上下文资料");
      availableModels = Array.isArray(data.models) ? data.models : [];
      modelSelect.replaceChildren();
      for (const model of availableModels) {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.name;
        modelSelect.append(option);
      }
      if (data.selected?.model_id) modelSelect.value = data.selected.model_id;
      modelSelect.disabled = availableModels.length === 0;
      updateModelContext(data.selected);
    }

    async function selectModel(modelId) {
      modelSelect.disabled = true;
      try {
        const response = await fetch("/api/v1/agents/patient/model", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ modelId }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "模型切换失败");
        updateModelContext(data.selected);
      } catch (error) {
        await loadModels().catch(() => {});
        modelContext.textContent = error.message;
      } finally {
        modelSelect.disabled = false;
      }
    }

    function newId(prefix) {
      return `${prefix}-${crypto.randomUUID()}`;
    }

    function renderAgentState(snapshot, view) {
      if (!snapshot || typeof snapshot !== "object" || !view) return;
      view.progress.hidden = false;
      view.status.textContent = snapshot.activity || "正在处理";
      view.todos.replaceChildren();
      for (const todo of Array.isArray(snapshot.todos) ? snapshot.todos : []) {
        if (!todo || typeof todo.content !== "string") continue;
        const item = document.createElement("li");
        item.textContent = todo.content;
        item.dataset.status = ["pending", "in_progress", "completed"].includes(todo.status) ? todo.status : "pending";
        view.todos.append(item);
      }
      view.todos.hidden = view.todos.childElementCount === 0;
      if (snapshot.model) {
        const model = snapshot.model;
        const budget = Number(model.input_budget_tokens || model.inputBudgetTokens || 0);
        const estimate = Number(model.estimated_input_tokens || 0);
        updateModelContext(model);
        view.usage.textContent = estimate && budget ? `输入估算 ${formatTokens(estimate)} / ${formatTokens(budget)}` : "";
        view.usage.hidden = !view.usage.textContent;
      }
    }

    function renderAgentTimeline(content, view) {
      if (!view) return;
      const entries = Array.isArray(content?.items) ? content.items : [];
      const agentNames = {
        patient: "患者主 Agent",
        report: "报告 Agent",
        insurance_cost: "医保与费用 Agent",
        psychological_support: "心理支持 Agent",
        exam_order: "检查与医嘱 Agent",
        transport_companion: "交通与陪同 Agent",
        symptom_safety_triage: "症状安全分流 Agent",
        consultation_prep: "问诊准备 Agent",
        home_care_request: "上门护理申请 Agent",
      };
      view.progress.hidden = false;
      view.timeline.replaceChildren();
      for (const entry of entries.slice().sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))) {
        const row = document.createElement("li");
        row.dataset.active = String(!/完成|结束|失败/.test(entry.status || ""));
        const dot = document.createElement("span");
        dot.className = "trace-dot";
        const main = document.createElement("span");
        main.className = "trace-main";
        const label = document.createElement("span");
        label.className = "trace-label";
        const rawName = (entry.agent || "patient").replaceAll(" ", "_");
        label.textContent = `${agentNames[rawName] || `专科 Agent · ${entry.agent}`} · ${entry.status || "正在处理"}`;
        const meta = document.createElement("span");
        meta.className = "trace-meta";
        meta.textContent = entry.model || "模型信息不可用";
        main.append(label, meta);
        const timing = document.createElement("span");
        timing.className = "trace-first-token";
        timing.textContent = entry.first_output_ms == null ? "等待首片段" : `首 token ${entry.first_output_ms} ms`;
        row.append(dot, main, timing);
        view.timeline.append(row);
      }
      view.timeline.scrollTop = 0;
    }

    function profileCard(title, value, subtitle = "") {
      const card = document.createElement("article");
      card.className = "profile-card";
      const heading = document.createElement("h3");
      heading.textContent = title;
      card.append(heading);
      if (subtitle) {
        const meta = document.createElement("p");
        meta.textContent = subtitle;
        card.append(meta);
      }
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "查看完整内容";
      const pre = document.createElement("pre");
      pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      details.append(summary, pre);
      card.append(details);
      return card;
    }

    function renderProfileTab(tab) {
      activeProfileTab = tab;
      profileTabs.replaceChildren();
      const tabs = [["fhir", "FHIR 资料"], ["memory", "记忆与归档"], ["retrieval", "实际召回"], ["sessions", "会话记录"], ["storage", "存储位置"]];
      for (const [key, label] of tabs) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "profile-tab";
        button.textContent = label;
        button.setAttribute("aria-selected", String(key === tab));
        button.addEventListener("click", () => renderProfileTab(key));
        profileTabs.append(button);
      }
      profilePanel.replaceChildren();
      if (!profileData) return;
      let entries = [];
      if (tab === "fhir") {
        entries = Object.entries(profileData.fhir || {}).map(([name, value]) => profileCard(name, value));
      } else if (tab === "memory") {
        const summary = profileData.memoryProfile || {};
        const extraction = profileData.memoryExtraction || {};
        const extractionCounts = extraction.counts || {};
        const latestExtraction = extraction.latest;
        const extractionStatuses = { pending: "排队中", processing: "抽取中", done: "已完成", failed: "失败" };
        entries.push(profileCard(
          "回合结束后的记忆抽取",
          latestExtraction
            ? `${extractionStatuses[latestExtraction.status] || latestExtraction.status} · 写入 ${latestExtraction.memories_written || 0} 条${latestExtraction.last_error ? `\n错误：${latestExtraction.last_error}` : ""}`
            : "还没有抽取任务",
          `排队 ${extractionCounts.pending || 0} · 处理中 ${extractionCounts.processing || 0} · 失败 ${extractionCounts.failed || 0} · 最近任务 ${latestExtraction?.created_at || "无"}`,
        ));
        entries.push(profileCard(
          `每轮注入摘要 · revision ${summary.revision || 0}`,
          summary.summary || "暂无长期记忆摘要",
          `当前有效 ${summary.active_count || 0} 条 · 本轮纳入 ${summary.included_count || 0} 条`,
        ));
        entries.push(...(profileData.memories || []).map(item => profileCard(
          item.kind === "long_term" ? "长期记忆" : "会话归档",
          item.text,
          item.kind === "long_term"
            ? `${item.category} · ${item.status} · 权重 ${item.weight} · 重要度 ${item.importance}/5 · 置信 ${item.confidence} · ${item.source || "无来源"} · 有效至 ${item.expires_at || "未设过期"} · ${item.entry_id}`
            : `${item.source || "无来源"} · ${item.created_at || ""} · ${item.entry_id || ""}`,
        )));
      } else if (tab === "retrieval") {
        entries = (profileData.retrievals || []).map(item => profileCard(
          `查询：${item.query}`,
          item.results,
          `${item.created_at || ""} · 会话 ${item.conversation_id || "未关联"}`,
        ));
      } else if (tab === "sessions") {
        entries = (profileData.sessions || []).map(item => profileCard(
          item.title || "未命名会话",
          { messages: item.messages, agent_state: item.agent_state, pending_interrupts: item.pending_interrupts },
          `${item.status} · ${item.updated_at} · ${item.thread_id}`,
        ));
      } else {
        entries = [profileCard("数据落盘信息", profileData.storage || {})];
      }
      if (!entries.length) {
        const empty = document.createElement("div");
        empty.className = "profile-empty";
        empty.textContent = tab === "retrieval" ? "还没有记录到向量召回" : "这个分类目前没有记录";
        profilePanel.append(empty);
      } else profilePanel.append(...entries);
    }

    async function loadProfile() {
      profilePanel.replaceChildren();
      const loading = document.createElement("div");
      loading.className = "profile-empty";
      loading.textContent = "正在读取患者资料、会话和向量记忆…";
      profilePanel.append(loading);
      const id = encodeURIComponent(patientId.value.trim() || "demo-patient-0001");
      try {
        const response = await fetch(`/api/v1/agents/patient/profile?patientId=${id}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "读取个人中心失败");
        profileData = data;
        document.querySelector("#profileSubtitle").textContent = `${data.patientId} · 演示数据调试视图`;
        renderProfileTab(activeProfileTab);
      } catch (error) {
        const message = document.createElement("div");
        message.className = "profile-error";
        message.textContent = error.message;
        profilePanel.replaceChildren(message);
      }
    }

    function addMessage(role, content, attachments = []) {
      const item = { id: newId("msg"), role, content, attachments };
      state.messages.push(item);
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
      renderHistory();
      renderSessionCatalog();
    }

    async function initializeSessions() {
      await refreshSessionCatalog();
      const latest = sessionCatalog[0];
      if (latest) await loadSession(latest.thread_id);
      else renderHistory();
    }

    function renderMessage(message, pending = false, isError = false) {
      welcome.hidden = true;
      const row = document.createElement("div");
      row.className = `message ${message.role}${isError ? " message-error" : ""}`;
      const bubble = document.createElement("div");
      bubble.className = `bubble${pending ? " pending" : ""}`;
      const content = document.createElement("div");
      if (message.role === "assistant") renderMarkdown(content, message.content);
      else content.textContent = message.content;
      bubble.append(content);
      if (message.role === "user" && message.attachments?.length) {
        const names = document.createElement("div");
        names.className = "upload-status";
        names.textContent = `已附文件：${message.attachments.map(file => file.name).join("、")}`;
        bubble.append(names);
      }
      let progress = null;
      if (message.role === "assistant") {
        const section = document.createElement("section");
        section.className = "agent-progress";
        section.setAttribute("aria-live", "polite");
        section.hidden = true;
        const heading = document.createElement("div");
        heading.style.cssText = "display:flex;justify-content:space-between;align-items:center;gap:10px";
        const status = document.createElement("div");
        status.className = "agent-progress-title";
        const usage = document.createElement("div");
        usage.className = "model-usage";
        usage.hidden = true;
        heading.append(status, usage);
        const timeline = document.createElement("ol");
        timeline.className = "agent-timeline";
        timeline.setAttribute("aria-label", "Agent 实时运行轨迹");
        const todos = document.createElement("ol");
        todos.className = "agent-todos";
        section.append(heading, timeline, todos);
        bubble.append(section);
        progress = { progress: section, status, usage, timeline, todos };
        const avatar = document.createElement("div");
        avatar.className = "avatar";
        avatar.textContent = "✳";
        row.append(avatar, bubble);
      } else {
        row.append(bubble);
      }
      conversation.append(row);
      scrollToBottom();
      return { row, bubble, content, progress };
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
      modelSelect.disabled = busy || availableModels.length === 0;
      if (!busy) input.focus();
    }

    function showInterrupt(bubble, interrupts) {
      const panel = document.createElement("div");
      panel.className = "interrupt";
      const heading = document.createElement("p");
      heading.textContent = "请核对下面这项操作，再选择是否执行：";
      panel.append(heading);
      for (const item of interrupts) {
        if (typeof item.message !== "string" || !item.message.trim()) continue;
        const detail = document.createElement("pre");
        detail.className = "interrupt-detail";
        detail.textContent = item.message;
        panel.append(detail);
      }
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

    async function runAgent({ resume, uploadedFiles } = {}) {
      if (activeController) return;
      const runState = state;
      const lastUser = [...state.messages].reverse().find(message => message.role === "user");
      if (!lastUser) return;
      const assistant = addMessage("assistant", "");
      const rendered = renderMessage(assistant, true);
      let pendingInterrupts = resume ? [...(runState.pendingInterrupts || [])] : [];
      const controller = new AbortController();
      activeController = controller;
      setBusy(true);
      renderAgentState({ activity: "\u6b63\u5728\u8fde\u63a5\u670d\u52a1", todos: [] }, rendered.progress);
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
        if (uploadedFiles?.length) {
          body.context.push({ description: "uploadedFiles", value: JSON.stringify(uploadedFiles) });
        }
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
            if (event.type === "RUN_STARTED") {
              renderAgentState({ activity: "\u6b63\u5728\u5904\u7406\u8bf7\u6c42", todos: [] }, rendered.progress);
            } else if (event.type === "STATE_SNAPSHOT") {
              renderAgentState(event.snapshot, rendered.progress);
            } else if (event.type === "ACTIVITY_SNAPSHOT") {
              renderAgentTimeline(event.content, rendered.progress);
            } else if (event.type === "TEXT_MESSAGE_CONTENT") {
              assistant.content += event.delta || "";
              renderMarkdown(rendered.content, assistant.content);
              rendered.bubble.classList.add("pending");
              scrollToBottom();
            } else if (event.type === "RUN_FINISHED") {
              finished = true;
              rendered.bubble.classList.remove("pending");
              const outcome = event.outcome;
              pendingInterrupts = outcome?.type === "interrupt" && Array.isArray(outcome.interrupts)
                ? outcome.interrupts
                : [];
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
        renderMarkdown(rendered.content, assistant.content);
        if (pendingInterrupts.length) showInterrupt(rendered.bubble, pendingInterrupts);
        rendered.bubble.classList.remove("pending");
        refreshSessionCatalog().catch(error => console.warn("刷新会话列表失败", error));
        if (activeController === controller) activeController = null;
        setBusy(false);
      }
      if (streamError) console.error("Agent SSE 请求失败", streamError);
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      const content = input.value.trim();
      if ((!content && !pendingUploads.length) || activeController || uploadingFiles) return;
      const attachments = pendingUploads;
      const extractedFiles = attachments.map(({ name, text }) => ({ name, text }));
      addMessage("user", content || "请阅读我上传的文件。", attachments.map(({ name }) => ({ name })));
      pendingUploads = [];
      renderUploadStatus();
      input.value = "";
      renderMessage(state.messages.at(-1));
      runAgent({ uploadedFiles: extractedFiles });
    });

    uploadButton.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      const selected = [...fileInput.files];
      fileInput.value = "";
      if (!selected.length) return;
      if (selected.length + pendingUploads.length > 5) {
        uploadStatus.textContent = "一次最多添加 5 个文件";
        return;
      }
      uploadingFiles = true;
      uploadButton.disabled = true;
      uploadStatus.textContent = "正在识别文件…";
      try {
        const formData = new FormData();
        selected.forEach(file => formData.append("files", file));
        const response = await fetch("/api/v1/agents/patient/files/extract", { method: "POST", body: formData });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "文件识别失败");
        pendingUploads.push(...result.files);
        uploadStatus.textContent = result.truncated ? "识别完成，内容较长，已截断" : "识别完成";
        renderUploadStatus();
      } catch (error) {
        uploadStatus.textContent = error.message;
      } finally {
        uploadingFiles = false;
        uploadButton.disabled = false;
      }
    });

    function renderUploadStatus() {
      uploadStatus.replaceChildren();
      for (const [index, file] of pendingUploads.entries()) {
        const chip = document.createElement("span");
        chip.style.cssText = "display:inline-flex;align-items:center;gap:5px;margin-right:8px;padding:3px 7px;border-radius:7px;background:#eef2ed";
        const name = document.createElement("span");
        name.textContent = file.name;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.setAttribute("aria-label", `移除 ${file.name}`);
        remove.textContent = "×";
        remove.addEventListener("click", () => {
          pendingUploads.splice(index, 1);
          renderUploadStatus();
        });
        chip.append(name, remove);
        uploadStatus.append(chip);
      }
    }

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
      pendingUploads = [];
      uploadStatus.replaceChildren();
      renderHistory();
      renderSessionCatalog();
      input.focus();
    });
    modelSelect.addEventListener("change", () => selectModel(modelSelect.value));
    document.querySelector("#openProfile").addEventListener("click", () => {
      profileDialog.showModal();
      loadProfile();
    });
    document.querySelector("#reloadProfile").addEventListener("click", loadProfile);
    document.querySelector("#closeProfile").addEventListener("click", () => profileDialog.close());

    patientId.value = state.patientId || "demo-patient-0001";
    patientId.addEventListener("change", () => refreshSessionCatalog().catch(error => console.warn("\u5237\u65b0\u4f1a\u8bdd\u5217\u8868\u5931\u8d25", error)));
    document.querySelectorAll(".suggestion").forEach(button => {
      button.addEventListener("click", () => {
        input.value = button.textContent;
        form.requestSubmit();
      });
    });
    renderHistory();
    initializeSessions().catch(error => console.error("加载服务端会话失败", error));
    loadModels().catch(error => {
      modelContext.textContent = error.message;
      modelSelect.replaceChildren(new Option("模型能力不可用", ""));
    });

    fetch("/health").then(response => {
      if (!response.ok) throw new Error("offline");
      document.querySelector("#healthDot").classList.add("online");
      document.querySelector("#healthText").textContent = "服务已连接";
    }).catch(() => {
      document.querySelector("#healthText").textContent = "服务未连接";
    });
