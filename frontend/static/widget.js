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
    if (thinking) {
      bubble.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
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
        body: JSON.stringify({ site_id: siteId, messages: chatHistory }),
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
          bubble.textContent = answer;
          messagesNode.scrollTop = messagesNode.scrollHeight;
        }
      }
      history.push({ role: "assistant", content: answer });
      if (!answer) bubble.textContent = "I couldn't generate a response. Please try again.";
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
