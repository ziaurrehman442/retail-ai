(() => {
  const script = document.currentScript;
  const siteId = script?.dataset.siteId;
  if (!siteId || !script) {
    console.error("Nimbus widget needs a data-site-id on its script tag.");
    return;
  }

  const apiBase = new URL(script.src).origin;
  const host = document.createElement("div");
  host.setAttribute("data-nimbus-widget", "");
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
    <style>
      :host{all:initial;color:#24243a;font-family:Arial,Helvetica,sans-serif}
      *,*::before,*::after{box-sizing:border-box}
      .launcher{position:fixed;right:24px;bottom:24px;z-index:2147483646;width:58px;height:58px;display:grid;place-items:center;border:0;border-radius:50%;background:#725be0;color:#fff;box-shadow:0 8px 26px #38218e4d;cursor:pointer;font-size:25px;transition:transform .2s,box-shadow .2s}
      .launcher:hover{transform:translateY(-3px);box-shadow:0 11px 30px #38218e61}
      .panel{position:fixed;right:24px;bottom:96px;z-index:2147483647;width:min(370px,calc(100vw - 32px));height:min(540px,calc(100dvh - 125px));display:none;flex-direction:column;overflow:hidden;border:1px solid #eceaf2;border-radius:17px;background:#fff;box-shadow:0 18px 70px #28234129;animation:rise .2s ease-out}
      .panel.open{display:flex}
      @keyframes rise{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}
      .head{height:70px;flex:none;display:flex;align-items:center;gap:11px;padding:0 17px;background:#725be0;color:#fff}
      .logo{width:36px;height:36px;display:grid;place-items:center;border-radius:11px;background:#ffffff24;font-size:20px}
      .title{min-width:0;flex:1;font-size:14px;font-weight:700}
      .online{display:block;margin-top:5px;color:#e6e0ff;font-size:10px;font-weight:400}
      .online i{display:inline-block;width:7px;height:7px;margin-right:5px;border-radius:50%;background:#88e1ba}
      .close{width:31px;height:31px;border:0;border-radius:50%;background:#ffffff19;color:#fff;cursor:pointer;font-size:20px}
      .messages{flex:1;overflow:auto;padding:17px 13px;background:#fff;scroll-behavior:smooth}
      .day{text-align:center;color:#a2a1b0;font-size:9px;letter-spacing:1px}
      .row{display:flex;align-items:flex-end;gap:7px;margin-top:16px}
      .row.user{justify-content:flex-end}
      .mini{width:24px;height:24px;flex:none;display:grid;place-items:center;border-radius:8px;background:#f0edff;color:#725be0;font-size:13px}
      .bubble{max-width:83%;padding:11px 12px;border:1px solid #eeedf3;border-radius:5px 13px 13px;background:#f8f8fb;color:#414256;font-size:12px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
      .user .bubble{border:0;border-radius:13px 5px 13px 13px;background:#725be0;color:white}
      .bubble.markdown{white-space:normal}
      .markdown p{margin:0 0 .65em}.markdown>:first-child{margin-top:0}.markdown>:last-child{margin-bottom:0}
      .markdown h1,.markdown h2,.markdown h3,.markdown h4,.markdown h5,.markdown h6{margin:.55em 0 .35em;color:inherit;font:inherit;font-weight:700}
      .markdown ul,.markdown ol{margin:.45em 0 .7em;padding-left:1.5em}.markdown li+li{margin-top:.22em}
      .markdown blockquote{margin:.5em 0;padding-left:.8em;border-left:2px solid #c9c0ea;color:#74738a}
      .markdown a{color:#6955c9;text-decoration:underline;text-underline-offset:2px}.user .markdown a{color:#fff}
      .markdown code{padding:.1em .35em;border-radius:4px;background:#eeedf4;color:#514b72;font-family:Consolas,monospace;font-size:.92em}
      .markdown pre{max-width:100%;overflow-x:auto;margin:.5em 0;padding:.7em;border-radius:7px;background:#eeedf4}
      .markdown pre code{padding:0;background:transparent}
      .markdown hr{margin:.7em 0;border:0;border-top:1px solid #e5e3ed}
      .markdown table{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse}
      .markdown th,.markdown td{padding:.3em .5em;border:1px solid #e5e3ed;text-align:left}.markdown th{font-weight:700}
      .welcome{margin-top:14px}
      .quick{display:flex;flex-wrap:wrap;gap:7px;margin:15px 0 0 30px}
      .quick button{padding:8px 10px;border:1px solid #e7e2f6;border-radius:18px;background:#fff;color:#6b58ba;cursor:pointer;font-size:10px}
      .composer{flex:none;display:flex;align-items:center;gap:8px;margin:0 12px;padding:10px 0;border-top:1px solid #f0eff4}
      .composer input{flex:1;min-width:0;height:40px;padding:0 12px;border:1px solid #ebeaf0;border-radius:10px;outline:none;color:#35354a;font:12px Arial,sans-serif}
      .composer input:focus{border-color:#a899ed;box-shadow:0 0 0 3px #f1edff}
      .composer button{width:40px;height:40px;border:0;border-radius:10px;background:#725be0;color:#fff;cursor:pointer;font-size:18px}
      .composer button:disabled{background:#d9d5ea;cursor:default}
      .powered{flex:none;padding:0 0 10px;text-align:center;color:#aaa9b5;font-size:8px;letter-spacing:1px}
      .powered b{color:#747087;font-size:10px}
      .dots{display:inline-flex;gap:4px;align-items:center;height:12px}
      .dots i{width:5px;height:5px;border-radius:50%;background:#8c80bd;animation:blink .9s infinite}
      .dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
      @keyframes blink{0%,65%,100%{opacity:.35;transform:translateY(0)}32%{opacity:1;transform:translateY(-4px)}}
      @media(max-width:480px){.launcher{right:16px;bottom:16px}.panel{right:10px;bottom:84px;width:calc(100vw - 20px);height:min(560px,calc(100dvh - 105px))}}
      @media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important}}
    </style>
    <button class="launcher" aria-label="Open chat with our assistant" aria-expanded="false">✳</button>
    <section class="panel" role="dialog" aria-label="Customer support chat" aria-modal="false">
      <header class="head"><span class="logo">✳</span><div class="title"><span class="business">AI assistant</span><span class="online"><i></i> Online · usually replies instantly</span></div><button class="close" aria-label="Close chat">×</button></header>
      <div class="messages" aria-live="polite"><div class="day">TODAY</div></div>
      <form class="composer"><input aria-label="Your message" placeholder="Ask us anything..." maxlength="4000" autocomplete="off"><button aria-label="Send message" disabled>➤</button></form>
      <div class="powered">POWERED BY <b>nimbus</b> ✦</div>
    </section>`;

  const launcher = shadow.querySelector(".launcher");
  const panel = shadow.querySelector(".panel");
  const close = shadow.querySelector(".close");
  const form = shadow.querySelector(".composer");
  const input = shadow.querySelector(".composer input");
  const send = shadow.querySelector(".composer button");
  const messagesNode = shadow.querySelector(".messages");
  const history = [];
  let busy = false;
  let visitorId;
  try {
    const visitorKey = `nimbus-visitor-${siteId}`;
    visitorId = window.localStorage.getItem(visitorKey) || window.crypto.randomUUID().replace(/-/g, "");
    window.localStorage.setItem(visitorKey, visitorId);
  } catch {
    visitorId = window.crypto.randomUUID().replace(/-/g, "");
  }

  const safeHref = (value) => {
    try {
      const url = new URL(value);
      return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
    } catch {
      return null;
    }
  };

  const renderInline = (parent, text) => {
    const pattern = /(\*\*.+?\*\*|__.+?__|~~.+?~~|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\((?:https?:\/\/|mailto:)[^)]+\)|https?:\/\/[^\s<]+)/g;
    let offset = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > offset) parent.appendChild(document.createTextNode(text.slice(offset, match.index)));
      const token = match[0];
      const link = token.match(/^\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)]+)\)$/);
      let element;
      if (link) {
        const href = safeHref(link[2]);
        if (!href) {
          parent.appendChild(document.createTextNode(token));
          offset = pattern.lastIndex;
          continue;
        }
        element = document.createElement("a");
        element.href = href;
        element.target = "_blank";
        element.rel = "noopener noreferrer";
        renderInline(element, link[1]);
      } else if (token.startsWith("**") || token.startsWith("__")) {
        element = document.createElement("strong");
        renderInline(element, token.slice(2, -2));
      } else if (token.startsWith("~~")) {
        element = document.createElement("del");
        renderInline(element, token.slice(2, -2));
      } else if (token.startsWith("`")) {
        element = document.createElement("code");
        element.textContent = token.slice(1, -1);
      } else if (token.startsWith("*") || token.startsWith("_")) {
        element = document.createElement("em");
        renderInline(element, token.slice(1, -1));
      } else {
        const trimmed = token.replace(/[.,!?;:]+$/, "");
        const href = safeHref(trimmed);
        if (!href) {
          parent.appendChild(document.createTextNode(token));
          offset = pattern.lastIndex;
          continue;
        }
        element = document.createElement("a");
        element.href = href;
        element.target = "_blank";
        element.rel = "noopener noreferrer";
        element.textContent = trimmed;
        if (trimmed.length < token.length) {
          parent.appendChild(element);
          parent.appendChild(document.createTextNode(token.slice(trimmed.length)));
          offset = pattern.lastIndex;
          continue;
        }
      }
      parent.appendChild(element);
      offset = pattern.lastIndex;
    }
    if (offset < text.length) parent.appendChild(document.createTextNode(text.slice(offset)));
  };

  const renderMarkdown = (container, markdown) => {
    container.replaceChildren();
    const lines = markdown.split(/\r?\n/);
    let index = 0;
    const heading = /^(#{1,6})\s+(.+)$/;
    const unordered = /^\s*[-*+]\s+(.+)$/;
    const ordered = /^\s*(\d+)[.)]\s+(.+)$/;
    const isBlockStart = (line) => heading.test(line) || unordered.test(line) ||
      line.startsWith("```") || ordered.test(line) || /^\s*>\s?/.test(line) ||
      /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line);
    const appendParagraph = (paragraph) => {
      const element = document.createElement("p");
      paragraph.forEach((line, lineIndex) => {
        if (lineIndex) element.appendChild(document.createElement("br"));
        renderInline(element, line);
      });
      container.appendChild(element);
    };

    while (index < lines.length) {
      const line = lines[index].trim();
      if (!line) {
        index += 1;
        continue;
      }
      if (line.startsWith("```")) {
        const code = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith("```")) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const pre = document.createElement("pre");
        const codeElement = document.createElement("code");
        codeElement.textContent = code.join("\n");
        pre.appendChild(codeElement);
        container.appendChild(pre);
        continue;
      }
      const headingMatch = line.match(heading);
      if (headingMatch) {
        const element = document.createElement(`h${headingMatch[1].length}`);
        renderInline(element, headingMatch[2]);
        container.appendChild(element);
        index += 1;
        continue;
      }
      if (line.includes("|") && index + 1 < lines.length &&
          /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1]) && lines[index + 1].includes("-")) {
        const splitRow = (value) => value.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
        const headers = splitRow(line);
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        headers.forEach((cell) => {
          const header = document.createElement("th");
          renderInline(header, cell);
          headerRow.appendChild(header);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);
        const tbody = document.createElement("tbody");
        index += 2;
        while (index < lines.length && lines[index].trim().includes("|")) {
          const cells = splitRow(lines[index]);
          const row = document.createElement("tr");
          headers.forEach((_, cellIndex) => {
            const cell = document.createElement("td");
            renderInline(cell, cells[cellIndex] || "");
            row.appendChild(cell);
          });
          tbody.appendChild(row);
          index += 1;
        }
        table.appendChild(tbody);
        container.appendChild(table);
        continue;
      }
      if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
        container.appendChild(document.createElement("hr"));
        index += 1;
        continue;
      }
      const orderedMatch = line.match(ordered);
      if (line.match(unordered) || orderedMatch) {
        const list = document.createElement(orderedMatch ? "ol" : "ul");
        if (orderedMatch) list.start = Number(orderedMatch[1]);
        while (index < lines.length) {
          const item = lines[index].trim().match(orderedMatch ? ordered : unordered);
          if (!item) break;
          const element = document.createElement("li");
          renderInline(element, orderedMatch ? item[2] : item[1]);
          list.appendChild(element);
          index += 1;
        }
        container.appendChild(list);
        continue;
      }
      if (line.startsWith(">")) {
        const quote = document.createElement("blockquote");
        const parts = [];
        while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
          parts.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        renderInline(quote, parts.join(" "));
        container.appendChild(quote);
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index].trim())) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      appendParagraph(paragraph);
    }
  };

  const addMessage = (role, content, thinking = false) => {
    const row = document.createElement("div");
    row.className = `row ${role}`;
    if (role === "assistant") {
      const icon = document.createElement("span");
      icon.className = "mini";
      icon.textContent = "✳";
      row.appendChild(icon);
    }
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (role === "assistant") bubble.classList.add("markdown");
    if (thinking) {
      bubble.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    } else if (role === "assistant") {
      renderMarkdown(bubble, content);
    } else {
      bubble.textContent = content;
    }
    row.appendChild(bubble);
    messagesNode.appendChild(row);
    messagesNode.scrollTop = messagesNode.scrollHeight;
    return bubble;
  };

  const sendMessage = async (text) => {
    const question = text.trim();
    if (!question || busy) return;
    busy = true;
    send.disabled = true;
    const chatHistory = [...history, { role: "user", content: question }];
    history.push({ role: "user", content: question });
    addMessage("user", question);
    const bubble = addMessage("assistant", "", true);
    input.value = "";
    let answer = "";
    try {
      const response = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, messages: chatHistory, visitor_id: visitorId }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || `Chat unavailable (${response.status}).`);
      }
      if (!response.body) throw new Error("The response stream was unavailable.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const events = pending.split("\n\n");
        pending = events.pop() || "";
        for (const event of events) {
          const data = event.split("\n").find((line) => line.startsWith("data: "));
          if (!data) continue;
          const payload = JSON.parse(data.slice(6));
          if (payload.error) throw new Error(payload.error);
          answer += payload.text || "";
          renderMarkdown(bubble, answer);
          messagesNode.scrollTop = messagesNode.scrollHeight;
        }
      }
      history.push({ role: "assistant", content: answer });
      if (!answer) renderMarkdown(bubble, "I couldn't generate a response. Please try again.");
    } catch (error) {
      bubble.textContent = error instanceof Error ? error.message : "Sorry, the assistant couldn't respond.";
    } finally {
      busy = false;
      send.disabled = !input.value.trim();
      input.focus();
    }
  };

  launcher.addEventListener("click", () => {
    const open = !panel.classList.contains("open");
    panel.classList.toggle("open", open);
    launcher.setAttribute("aria-expanded", String(open));
    launcher.setAttribute("aria-label", open ? "Close chat" : "Open chat with our assistant");
    if (open) input.focus();
  });
  close.addEventListener("click", () => launcher.click());
  input.addEventListener("input", () => { send.disabled = busy || !input.value.trim(); });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendMessage(input.value);
  });

  fetch(`${apiBase}/api/public/sites/${encodeURIComponent(siteId)}`)
    .then(async (response) => {
      if (!response.ok) throw new Error("This website assistant could not be found.");
      return response.json();
    })
    .then((site) => {
      shadow.querySelector(".business").textContent = site.name;
      addMessage("assistant", `Hi there! 👋 I'm the ${site.name} assistant. How can I help you today?`);
      const shortcuts = document.createElement("div");
      shortcuts.className = "quick";
      ["What do you offer?", "How can I contact you?"].forEach((question) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = question;
        button.addEventListener("click", () => void sendMessage(question));
        shortcuts.appendChild(button);
      });
      messagesNode.appendChild(shortcuts);
    })
    .catch((error) => {
      console.error("Nimbus widget could not load the website assistant.", error);
      addMessage("assistant", "Sorry, this chat assistant is temporarily unavailable.");
    });
})();
