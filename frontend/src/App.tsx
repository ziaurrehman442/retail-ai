import { FormEvent, useEffect, useMemo, useState } from "react";
import { Markdown } from "./Markdown";
import { AuthScreen, type AuthUser } from "./AuthScreen";
import { AdminPanel } from "./AdminPanel";
import { BillingPanel } from "./BillingPanel";
import "./markdown.css";

type Site = { id: string; name: string; website: string; knowledge: string };
type Message = { role: "user" | "assistant"; content: string };
type Usage = {
  plan: "free" | "pro" | "custom";
  requests: number;
  successful: number;
  unsuccessful: number;
  avg_response_ms: number | null;
  daily: { day: string; requests: number; successful: number }[];
  websites: number;
};
type Page = "Overview" | "Knowledge base" | "Install widget" | "Settings" | "Admin";

let csrfToken = "";

const pageIds: Record<Page, string> = {
  Overview: "overview",
  "Knowledge base": "knowledge",
  "Install widget": "install",
  Settings: "settings",
  Admin: "admin",
};

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error || `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    grid: "▦", book: "▤", code: "‹/›", settings: "⚙", spark: "✳",
    globe: "◎", arrow: "↗", check: "✓", send: "➤",
  };
  return <span className={`icon icon-${name}`} aria-hidden="true">{paths[name] || "•"}</span>;
}

function getVisitorId(siteId: string) {
  const key = `nexa-visitor-${siteId}`;
  try {
    const existing = window.localStorage.getItem(key);
    if (existing) return existing;
    const created = window.crypto.randomUUID().replace(/-/g, "");
    window.localStorage.setItem(key, created);
    return created;
  } catch {
    return window.crypto.randomUUID().replace(/-/g, "");
  }
}

function Chat({
  site,
  knowledge,
  messages,
  setMessages,
}: {
  site: Site;
  knowledge: string;
  messages: Message[];
  setMessages: (messages: Message[]) => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = Boolean(knowledge.trim());

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy || !ready) return;
    const history = [...messages, { role: "user" as const, content: text }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: site.id, messages: history, visitor_id: getVisitorId(site.id) }),
      });
      if (!response.ok) {
        const error = (await response.json()) as { error?: string };
        throw new Error(error.error || "The assistant could not respond.");
      }
      if (!response.body) throw new Error("The response stream was unavailable.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let answer = "";
      let pending = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const lines = pending.split("\n");
        pending = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6)) as { text?: string; error?: string };
            if (event.error) throw new Error(event.error);
            answer += event.text || "";
          } catch (error) {
            if (error instanceof SyntaxError) continue;
            throw error;
          }
        }
        setMessages([...history, { role: "assistant", content: answer }]);
      }
      pending += decoder.decode();
      if (pending.startsWith("data: ")) {
        const event = JSON.parse(pending.slice(6)) as { text?: string; error?: string };
        if (event.error) throw new Error(event.error);
        answer += event.text || "";
      }
      setMessages([...history, { role: "assistant", content: answer || "I could not generate a response. Please try again." }]);
    } catch (error) {
      setMessages([...history, { role: "assistant", content: error instanceof Error ? error.message : "The assistant could not respond." }]);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  return (
    <div className="chat-frame">
      <div className="chat-brand"><span className="chat-logo"><Icon name="spark" /></span><div><b>{site.name}</b><small><i /> Usually replies instantly</small></div><span className="chat-menu">···</span></div>
      <div className="chat-messages">
        <div className="chat-day">TODAY</div>
        {!ready && <div className="chat-empty-hint">Add business information to start testing your assistant.</div>}
        {messages.map((message, index) => <div className={`chat-message ${message.role}`} key={`${index}-${message.role}`}>
          {message.role === "assistant" && <span className="mini-chat-logo"><Icon name="spark" /></span>}
          <div className="bubble">{message.content
            ? message.role === "assistant" ? <Markdown>{message.content}</Markdown> : message.content
            : busy ? <span className="thinking-dots"><i /><i /><i /></span> : null}</div>
        </div>)}
        {messages.length === 1 && ready && <div className="quick-questions"><button onClick={() => void send("What does your business do?")}>What do you offer?</button><button onClick={() => void send("What are your business hours?")}>Opening hours</button></div>}
      </div>
      <form className="chat-input" onSubmit={submit}><input value={input} onChange={(event) => setInput(event.target.value)} placeholder={ready ? "Ask us anything..." : "Add business details to chat"} aria-label="Ask the assistant" disabled={!ready || busy} /><button aria-label="Send message" disabled={!ready || busy || !input.trim()}><Icon name="send" /></button></form>
      <div className="powered-by">POWERED BY <b>nexa</b> ✦</div>
    </div>
  );
}

export default function App() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [sites, setSites] = useState<Site[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [activeId, setActiveId] = useState("");
  const [page, setPage] = useState<Page>("Overview");
  const [messages, setMessages] = useState<Message[]>([{ role: "assistant", content: "Hi there! 👋 I'm your AI assistant. How can I help you today?" }]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [crawling, setCrawling] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [newWebsite, setNewWebsite] = useState("");
  const activeSite = sites.find((site) => site.id === activeId);
  const knowledge = activeSite?.knowledge || "";
  const isTrained = knowledge.trim().length > 0;

  useEffect(() => {
    api<{ authenticated: boolean; user?: AuthUser; csrf_token?: string }>("/api/auth/me")
      .then((result) => {
        if (result.authenticated && result.user && result.csrf_token) {
          csrfToken = result.csrf_token;
          setAuthUser(result.user);
        }
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not check sign-in status."))
      .finally(() => setAuthReady(true));
  }, []);

  useEffect(() => {
    if (!authUser) {
      setSites([]);
      setActiveId("");
      setLoading(false);
      return;
    }
    setLoading(true);
    api<Site[]>("/api/sites")
      .then((items) => {
        setSites(items);
        setActiveId((current) => items.some((site) => site.id === current) ? current : items[0]?.id || "");
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load website workspaces."))
      .finally(() => setLoading(false));
  }, [authUser]);

  useEffect(() => {
    if (!authUser) {
      setUsage(null);
      return;
    }
    api<Usage>("/api/usage?days=7")
      .then(setUsage)
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load usage analytics."));
  }, [authUser]);

  useEffect(() => {
    setMessages([{ role: "assistant", content: `Hi there! 👋 I'm the ${activeSite?.name || "AI"} assistant. How can I help you today?` }]);
  }, [activeId, activeSite?.name]);

  useEffect(() => {
    const onPop = () => {
      const entry = (Object.entries(pageIds) as [Page, string][]).find(([, value]) => value === window.location.hash.slice(1));
      setPage(entry?.[0] || "Overview");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const snippet = useMemo(() => {
    if (!activeSite) return "Select a website workspace to generate its widget code.";
    return `<script src="${window.location.origin}/widget.js" data-site-id="${activeSite.id}" async></script>`;
  }, [activeSite]);

  function navigate(next: Page) {
    setPage(next);
    window.history.replaceState(null, "", `#${pageIds[next]}`);
  }

  async function updateActive(changes: Partial<Site>) {
    if (!activeSite) return;
    setSaving(true);
    setError("");
    try {
      const updated = await api<Site>(`/api/sites/${activeSite.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: changes.name ?? activeSite.name,
          website: changes.website ?? activeSite.website,
          knowledge: changes.knowledge ?? activeSite.knowledge,
        }),
      });
      setSites((current) => current.map((site) => site.id === updated.id ? updated : site));
      showToast("Your business details are saved.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save your changes.");
    } finally {
      setSaving(false);
    }
  }

  async function crawlActiveWebsite() {
    if (!activeSite?.website.trim() || crawling) return;
    const marker = "\n\n--- Website content fetched from ";
    const existingNotes = activeSite.knowledge.split(marker)[0].trim();
    const maxCharacters = 20000 - existingNotes.length - 400;
    if (maxCharacters < 500) {
      setError("Shorten the existing business information to make room for imported website content.");
      return;
    }
    setCrawling(true);
    setError("");
    try {
      const result = await api<{ knowledge: string; pages: number; url: string }>(
        `/api/sites/${activeSite.id}/crawl`,
        {
          method: "POST",
          body: JSON.stringify({ website: activeSite.website, max_characters: maxCharacters }),
        },
      );
      const imported = `${marker}${result.url} ---\n${result.knowledge}`;
      const combined = [existingNotes, imported].filter(Boolean).join("\n\n");
      if (combined.length > 20000) {
        setError("The imported content exceeds the 20,000-character limit. Shorten the existing notes, then fetch again.");
        return;
      }
      setSites((current) => current.map((site) =>
        site.id === activeSite.id ? { ...site, knowledge: combined } : site,
      ));
      showToast(`Fetched ${result.pages} website page${result.pages === 1 ? "" : "s"}. Review and save your changes.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not fetch website content.");
    } finally {
      setCrawling(false);
    }
  }

  function showToast(text: string) {
    setToast(text);
    window.setTimeout(() => setToast(""), 2600);
  }

  async function addSite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const created = await api<Site>("/api/sites", {
        method: "POST",
        body: JSON.stringify({ name: newName, website: newWebsite }),
      });
      setSites((current) => [...current, created]);
      setActiveId(created.id);
      setNewName("");
      setNewWebsite("");
      showToast("Website workspace created.");
      navigate("Knowledge base");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the website workspace.");
    }
  }

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(snippet);
      showToast("Widget code copied to clipboard.");
    } catch {
      setError("Clipboard access is unavailable. Select and copy the widget code instead.");
    }
  }

  const doneSteps = 1 + Number(isTrained) + Number(Boolean(activeSite?.website));
  const responseTime = usage?.avg_response_ms === null || usage?.avg_response_ms === undefined
    ? "—"
    : usage.avg_response_ms < 1000
      ? `${Math.round(usage.avg_response_ms)} ms`
      : `${Number((usage.avg_response_ms / 1000).toFixed(2))} sec`;
  const successRate = usage && usage.requests
    ? `${Math.round(usage.successful / usage.requests * 100)}%`
    : usage ? "—" : "…";
  const maxActivity = Math.max(1, ...(usage?.daily.map((item) => item.requests) || []));

  if (!authReady) {
    return <main className="auth-screen"><div className="admin-loading">Loading your secure workspace…</div></main>;
  }

  if (!authUser) {
    return <AuthScreen onAuthenticated={(user, token) => {
      csrfToken = token;
      setAuthUser(user);
      setError("");
    }} />;
  }

  async function signOut() {
    try {
      await api<{ ok: boolean }>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
      csrfToken = "";
      setAuthUser(null);
      setPage("Overview");
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sign out.");
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" onClick={(event) => { event.preventDefault(); navigate("Overview"); }}><span className="brand-mark">✳</span><span>nexa<span className="brand-period">.</span></span></a>
        <div className="side-caption">WORKSPACE</div>
        <label className="workspace-switch"><span className="workspace-initial">{activeSite?.name?.[0]?.toUpperCase() || "N"}</span><span className="workspace-meta"><b>{activeSite?.name || (loading ? "Loading..." : "No websites")}</b><small>{usage?.plan || authUser.plan} plan · {sites.length} site{sites.length === 1 ? "" : "s"}</small></span><span className="switch-chevron">⌄</span><select aria-label="Select a website" value={activeId} onChange={(event) => setActiveId(event.target.value)}>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
        <div className="side-caption side-caption-gap">BUILD</div><nav className="side-nav" aria-label="Main navigation">
          {(["Overview", "Knowledge base", "Install widget"] as Page[]).map((item, index) => <button key={item} className={`nav-link ${page === item ? "active" : ""}`} onClick={() => navigate(item)}><Icon name={["grid", "book", "code"][index]} />{item}{item === "Knowledge base" && <i className="nav-count">{isTrained ? "1" : "0"}</i>}</button>)}
        </nav>
        {authUser.role === "admin" && <button className={`nav-link ${page === "Admin" ? "active" : ""}`} onClick={() => navigate("Admin")}><Icon name="grid" />Admin analytics</button>}
        <div className="side-caption side-caption-gap">MANAGE</div><button className={`nav-link ${page === "Settings" ? "active" : ""}`} onClick={() => navigate("Settings")}><Icon name="settings" />Settings</button>
        <div className="sidebar-grow" /><section className="upgrade-card"><div className="upgrade-title"><span>✦</span> Make it yours</div><p>Upgrade for more conversations and advanced insights.</p><button onClick={() => navigate("Settings")}>Explore plans <span>↗</span></button></section>
        <div className="profile"><span className="profile-avatar">{authUser.email.slice(0, 2).toUpperCase()}</span><span className="profile-meta"><b>{authUser.email}</b><small>{authUser.role === "admin" ? "Administrator" : `${usage?.plan || authUser.plan} plan`}</small></span><button className="profile-more" title="Sign out" aria-label="Sign out" onClick={() => void signOut()}>↪</button></div>
      </aside>

      <section className="main-column"><header className="topbar"><div className="breadcrumbs"><span>Workspace</span><b>/</b><strong>{page}</strong></div><div className="top-actions"><span className="system-status"><i /> Assistant platform</span><span className="help-link">{authUser.role === "admin" ? "Platform admin" : "Workspace active"}</span><span className="notification-button">✦</span></div></header>
        <div className="page-content">
          {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error">×</button></div>}
          {page === "Overview" && <section className="page-view active">
            <div className="page-heading"><div><div className="eyebrow">YOUR AI SUPPORT WORKSPACE</div><h1>Good morning <span className="heading-spark">✳</span></h1><p>Set up your assistant, then let it take care of the questions.</p></div><button className="button-primary" onClick={() => navigate("Install widget")}><Icon name="code" /> <span>Install widget</span></button></div>
            <section className="welcome-banner"><div className="banner-decoration decor-one" /><div className="banner-decoration decor-two" /><div className="banner-copy"><div className="banner-kicker"><span>✦</span> YOUR AI ASSISTANT IS READY</div><h2>Your always-on<br />support team starts here.</h2><p>Teach your assistant about your business, then add it to your website in minutes.</p><button onClick={() => navigate("Knowledge base")}>Add your website details <span>→</span></button></div><div className="banner-art"><div className="art-card art-back"><i /><span /><span /></div><div className="art-card art-front"><span className="art-avatar">✳</span><span className="art-lines"><i /><i /></span><span className="art-chat">How can I help? <b>✦</b></span></div><span className="art-star">✦</span><span className="art-spark">✳</span></div></section>
            <div className="section-heading"><div><h2>Overview</h2><p>Your assistant at a glance</p></div><button className="select-period">Last 7 days <span>⌄</span></button></div>
            <div className="stats-grid"><article className="stat-card"><div className="stat-top"><span>Chat requests</span><i className="stat-icon lavender">▤</i></div><div className="stat-number">{usage?.requests.toLocaleString() ?? "…"}</div><div className="stat-description">Measured over the last 7 days</div></article><article className="stat-card"><div className="stat-top"><span>Successful answers</span><i className="stat-icon green">✦</i></div><div className="stat-number">{successRate}</div><div className="stat-description">{usage ? `${usage.successful} completed responses` : "Loading response data"}</div></article><article className="stat-card"><div className="stat-top"><span>Time to first response</span><i className="stat-icon peach">↗</i></div><div className="stat-number stat-latency">{responseTime}</div><div className="stat-description">From request until the first answer text</div></article><article className="stat-card"><div className="stat-top"><span>Website workspaces</span><i className="stat-icon yellow">◎</i></div><div className="stat-number">{usage?.websites ?? "…"}</div><div className="stat-description">Active workspaces on your account</div></article></div>
            <div className="lower-grid"><article className="panel activity-panel"><div className="panel-heading"><div><h3>Conversation activity</h3><p>Successful and incomplete requests over the last 7 days</p></div><button className="admin-refresh" onClick={() => void api<Usage>("/api/usage?days=7").then(setUsage).catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not refresh usage analytics."))}>Refresh</button></div>{usage?.daily.length ? <div className="usage-activity-chart">{usage.daily.map((day) => <div className="usage-activity-day" key={day.day} title={`${day.day}: ${day.requests} requests`}><span>{day.requests}</span><i style={{ height: `${Math.max(5, day.requests / maxActivity * 100)}%` }} /><small>{day.day.slice(5)}</small></div>)}</div> : <div className="chart-placeholder"><div className="chart-center"><span>✦</span><b>{usage ? "No chat requests yet" : "Loading activity…"}</b><small>Request totals will appear after visitors use your widget.</small></div></div>}</article><article className="panel checklist-panel"><div className="panel-heading"><div><h3>Getting started</h3><p>A few steps to get up and running</p></div><span className="steps-count">{doneSteps} of 3</span></div><div className="progress-track"><i style={{ width: `${(doneSteps / 3) * 100}%` }} /></div><div className="checklist"><button onClick={() => navigate("Settings")}><i className="step-check complete">✓</i><span><b>Create your workspace</b><small>Your assistant has a home</small></span></button><button onClick={() => navigate("Knowledge base")}><i className={`step-check ${isTrained ? "complete" : ""}`}>{isTrained ? "✓" : ""}</i><span><b>Teach your assistant</b><small>Add details about your business</small></span><span className="row-arrow">↗</span></button><button onClick={() => navigate("Install widget")}><i className={`step-check ${activeSite?.website ? "complete" : ""}`}>{activeSite?.website ? "✓" : ""}</i><span><b>Add it to your website</b><small>Copy one line of code</small></span><span className="row-arrow">↗</span></button></div></article></div>
          </section>}

          {page === "Knowledge base" && activeSite && <section className="page-view active"><div className="page-heading"><div><div className="eyebrow">HELP YOUR AI GIVE BETTER ANSWERS</div><h1>Knowledge base</h1><p>Share your business details so your assistant can answer customer questions.</p></div><button className="button-primary" disabled={saving} onClick={() => void updateActive({})}><Icon name="check" /><span>{saving ? "Saving..." : "Save changes"}</span></button></div><div className="knowledge-layout"><div className="knowledge-main"><article className="form-card"><div className="form-title"><span className="form-icon"><Icon name="globe" /></span><div><h3>Your website</h3><p>Fetch public pages to build your assistant&apos;s knowledge</p></div><span className="connected-status"><i /> WORKSPACE</span></div><label htmlFor="business-name">Business name</label><input id="business-name" value={activeSite.name} maxLength={100} onChange={(event) => setSites(sites.map((site) => site.id === activeId ? { ...site, name: event.target.value } : site))} /><label htmlFor="website-url">Website URL</label><div className="website-input"><span>◎</span><input id="website-url" value={activeSite.website} maxLength={300} placeholder="yourwebsite.com" onChange={(event) => setSites(sites.map((site) => site.id === activeId ? { ...site, website: event.target.value } : site))} /></div><button className="crawl-button" disabled={crawling || !activeSite.website.trim()} onClick={() => void crawlActiveWebsite()}><Icon name="globe" />{crawling ? "Reading your website..." : "Fetch website content"}</button><small className="field-hint">Fetches the homepage and up to four relevant pages on the same website. Review the imported content and save it below.</small></article><article className="form-card"><div className="form-title"><span className="form-icon lilac"><Icon name="book" /></span><div><h3>Business information</h3><p>What should your assistant know?</p></div></div><label htmlFor="business-knowledge">About your business</label><textarea id="business-knowledge" value={activeSite.knowledge} maxLength={20000} rows={10} placeholder="Fetch your website content above, or add your products, services, opening hours, delivery and return policies, FAQs, and other useful details here." onChange={(event) => setSites(sites.map((site) => site.id === activeId ? { ...site, knowledge: event.target.value } : site))} /><div className="textarea-meta"><span>{activeSite.knowledge.length.toLocaleString()} / 20,000</span><span>Review crawled content before saving it.</span></div></article><div className="learning-note"><span>✦</span><div><b>How answers stay relevant</b><p>Your saved business information is used to help answer customer questions with details specific to your business.</p></div></div></div><aside className="knowledge-preview"><div className="preview-header"><span className="presence-dot" /><div><b>Preview your assistant</b><small>Test a question about your business</small></div></div><Chat site={activeSite} knowledge={activeSite.knowledge} messages={messages} setMessages={setMessages} /></aside></div></section>}

          {page === "Install widget" && <section className="page-view active"><div className="page-heading"><div><div className="eyebrow">ONE LINE. THAT’S IT.</div><h1>Install your widget</h1><p>Add your AI assistant to any website in just a couple of minutes.</p></div><span className="works-label">✓ Works on any website</span></div><div className="install-layout"><div className="install-steps"><article className="install-card"><div className="install-title"><span>01</span><div><h3>Copy your embed code</h3><p>The assistant loads your business information securely from your server.</p></div></div><div className="code-window"><div className="code-header"><span><i /> YOUR EMBED CODE</span><button onClick={() => void copyCode()}>▣ &nbsp;Copy code</button></div><pre><code>{snippet}</code></pre></div><p className="code-security">✓ The embed code contains only your website ID, not your private business information.</p></article><article className="install-card"><div className="install-title"><span>02</span><div><h3>Paste it into your website</h3><p>Place the code just before the closing <code>&lt;/body&gt;</code> tag on each page.</p></div></div><div className="platform-list"><span>◉ Shopify</span><span>W WordPress</span><span>◎ Webflow</span><span>⌘ Custom site</span></div></article><article className="install-card"><div className="install-title"><span>03</span><div><h3>Check that it’s working</h3><p>Open your website and look for the chat button in the bottom corner.</p></div></div><div className="widget-check"><i>✓</i><span><b>{isTrained ? "Widget is ready to install" : "Add business information first"}</b><small>Your widget shows a floating button on your website.</small></span><button onClick={() => navigate("Knowledge base")}>Edit details ↗</button></div></article></div><aside className="install-preview"><div className="preview-toolbar"><span><i /> LIVE PREVIEW</span><button onClick={() => navigate("Knowledge base")}>Edit details ↗</button></div><div className="mock-website"><div className="browser-chrome"><i /><i /><i /><span>{activeSite?.website || "yourwebsite.com"}</span></div><div className="mock-content"><small>THE ART OF GIVING</small><h2>A little joy,<br />delivered.</h2><p>Thoughtful flowers for every kind of day.</p><button>Explore flowers&nbsp; →</button><span className="mock-flower flower-a">✿</span><span className="mock-flower flower-b">✿</span><span className="mock-flower flower-c">✿</span></div><div className="mock-floating-button"><Icon name="spark" /></div><div className="mock-tooltip">Need a hand? <span>✦</span></div></div><div className="preview-caption"><i /> Your widget is ready to preview</div></aside></div></section>}

          {page === "Settings" && <section className="page-view active"><div className="page-heading"><div><div className="eyebrow">YOUR WORKSPACE</div><h1>Settings</h1><p>Manage your account, plan, and website workspaces.</p></div></div><div className="settings-layout"><BillingPanel csrfToken={csrfToken} /><article className="form-card"><div className="form-title"><span className="form-icon"><Icon name="settings" /></span><div><h3>Website workspaces</h3><p>Your active website allowance is shown in Plans and usage.</p></div></div><div className="workspace-list">{sites.map((site) => <button className={`workspace-row ${site.id === activeId ? "selected" : ""}`} key={site.id} onClick={() => { setActiveId(site.id); navigate("Knowledge base"); }}><span className="workspace-initial">{site.name[0]?.toUpperCase() || "N"}</span><span><b>{site.name}</b><small>{site.website || "No website address"}</small></span><span className="row-arrow">↗</span></button>)}</div><form className="new-site-form" onSubmit={(event) => void addSite(event)}><label htmlFor="new-site-name">Add a website workspace</label><div><input id="new-site-name" placeholder="Business name" required maxLength={100} value={newName} onChange={(event) => setNewName(event.target.value)} /><input placeholder="Website URL (optional)" maxLength={300} value={newWebsite} onChange={(event) => setNewWebsite(event.target.value)} /><button className="button-primary">+ Add website</button></div></form></article></div></section>}

          {page === "Admin" && authUser.role === "admin" && <AdminPanel csrfToken={csrfToken} />}
          <footer className="page-footer"><span>© 2026 Nexa AI</span><span><button onClick={() => navigate("Settings")}>Plans and billing</button><button onClick={() => navigate("Install widget")}>Widget installation</button></span></footer>
        </div>
      </section>

      {page === "Overview" && <aside className="right-rail"><div className="rail-title"><span>WIDGET PREVIEW</span><button onClick={() => navigate("Install widget")}>↗</button></div><div className="rail-website"><div className="rail-browser"><i /><i /><i /><span>{activeSite?.website || "yourwebsite.com"}</span></div><div className="rail-site-body"><small>THE ART OF GIVING</small><h2>A little joy,<br />delivered.</h2><p>Thoughtful flowers for every kind of day.</p><span className="rail-shop-button">Explore flowers&nbsp; →</span><span className="rail-flower flower-one">✿</span><span className="rail-flower flower-two">✿</span><span className="rail-flower flower-three">✿</span></div><div className="rail-chat-button"><Icon name="spark" /></div><div className="rail-chat-hint">Need a hand? <span>✦</span></div></div><div className="rail-tip"><span className="tip-spark">✦</span><div><b>Looking good!</b><p>Your assistant is ready to learn about your business. Add details, then put it to work.</p><button onClick={() => navigate("Knowledge base")}>Set up your assistant <span>→</span></button></div></div><div className="rail-bottom"><span><i /> Widget preview</span><button onClick={() => navigate("Install widget")}>Get install code ↗</button></div></aside>}
      {toast && <div className="toast" role="status">{toast}</div>}
    </main>
  );
}
