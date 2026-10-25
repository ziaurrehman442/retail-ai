import { useEffect, useState } from "react";
import "./billing.css";

type Plan = "free" | "pro" | "custom";
type Network = "TRC20" | "ERC20" | "BEP20";
type MetricUser = {
  id: string; email: string; role: string; plan: Plan; status: "active" | "suspended";
  website_limit: number; subscription_expires_at: string | null;
  requests_in_period: number; successful: number | null; avg_ms: number | null; visitors: number;
};
type PaymentOrder = {
  id: string; requested_plan: Exclude<Plan, "free">; term_months: number;
  requested_websites: number; activated_websites: number | null; amount_usd: number;
  network: Network; wallet_address: string; transaction_hash: string; status: string;
  admin_note: string; created_at: string; reviewed_at: string | null; email: string;
};
type Metrics = {
  days: number;
  totals: { requests: number; visitors: number; successful: number; unsuccessful: number; avg_response_ms: number | null };
  daily: { day: string; requests: number; visitors: number; successful: number; avg_ms: number | null }[];
  users: MetricUser[];
  payment_orders: PaymentOrder[];
  plans: Record<Plan, { name: string; price_usd: number; monthly_requests: number | null; websites: number }>;
};
type BillingSettings = {
  plans: Record<"pro" | "custom", { name: string; price_per_site_month: number }>;
  term_discounts: Record<string, number>;
  wallets: Record<Network, string>;
};
type UserDraft = Pick<MetricUser, "plan" | "status" | "website_limit"> & { subscription_expires_at: string };
type ReviewDraft = { activated_websites: number; admin_note: string };

function latency(value: number | null) {
  return value === null
    ? "—"
    : value < 1000
      ? `${Math.round(value)} ms`
      : `${Number((value / 1000).toFixed(2))} sec`;
}

function dateTimeValue(value: string | null) {
  return value ? value.replace(" ", "T").slice(0, 16) : "";
}

export function AdminPanel({ csrfToken }: { csrfToken: string }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [settings, setSettings] = useState<BillingSettings | null>(null);
  const [userDrafts, setUserDrafts] = useState<Record<string, UserDraft>>({});
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, ReviewDraft>>({});
  const [days, setDays] = useState(30);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);

  async function loadMetrics() {
    const response = await fetch(`/api/admin/metrics?days=${days}`);
    const result = (await response.json()) as Metrics & { error?: string };
    if (!response.ok) throw new Error(result.error || "Could not load admin analytics.");
    setMetrics(result);
    setUserDrafts(Object.fromEntries(result.users.map((user) => [user.id, {
      plan: user.plan,
      status: user.status,
      website_limit: user.website_limit,
      subscription_expires_at: dateTimeValue(user.subscription_expires_at),
    }])));
    setReviewDrafts((current) => Object.fromEntries(result.payment_orders.map((order) => [
      order.id,
      current[order.id] || { activated_websites: order.requested_websites, admin_note: "" },
    ])));
  }

  async function loadSettings() {
    const response = await fetch("/api/admin/settings");
    const result = (await response.json()) as { billing?: BillingSettings; error?: string };
    if (!response.ok || !result.billing) throw new Error(result.error || "Could not load billing settings.");
    setSettings(result.billing);
  }

  async function refresh() {
    setError("");
    try {
      await Promise.all([loadMetrics(), loadSettings()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load admin data.");
    }
  }

  useEffect(() => { void refresh(); }, [days]);

  async function reviewPayment(order: PaymentOrder, approve: boolean) {
    const draft = reviewDrafts[order.id] || { activated_websites: order.requested_websites, admin_note: "" };
    setBusyId(order.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/admin/payment-orders/${order.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ approve, activated_websites: draft.activated_websites, admin_note: draft.admin_note }),
      });
      const result = (await response.json()) as { error?: string; warning?: string | null };
      if (!response.ok) throw new Error(result.error || "Could not review payment.");
      if (result.warning) setNotice(result.warning);
      await loadMetrics();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not review payment.");
    } finally {
      setBusyId("");
    }
  }

  async function saveSettings() {
    if (!settings) return;
    setSavingSettings(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/admin/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(settings),
      });
      const result = (await response.json()) as { error?: string };
      if (!response.ok) throw new Error(result.error || "Could not save billing settings.");
      setNotice("Pricing, term discounts, and payment wallets were saved.");
      await loadMetrics();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save billing settings.");
    } finally {
      setSavingSettings(false);
    }
  }

  async function saveUser(user: MetricUser) {
    const draft = userDrafts[user.id];
    if (!draft) return;
    setBusyId(user.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({
          ...draft,
          subscription_expires_at: draft.subscription_expires_at || null,
        }),
      });
      const result = (await response.json()) as { error?: string };
      if (!response.ok) throw new Error(result.error || "Could not update account.");
      setNotice(`Account ${user.email} was updated.`);
      await loadMetrics();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update account.");
    } finally {
      setBusyId("");
    }
  }

  async function deleteUser(user: MetricUser) {
    if (!window.confirm(`Permanently delete ${user.email} and its websites, analytics, and payment history?`)) return;
    setBusyId(user.id);
    setError("");
    try {
      const response = await fetch(`/api/admin/users/${user.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
      });
      const result = (await response.json()) as { error?: string };
      if (!response.ok) throw new Error(result.error || "Could not delete account.");
      await loadMetrics();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete account.");
    } finally {
      setBusyId("");
    }
  }

  function updateSettings(
    section: "plans" | "term_discounts" | "wallets",
    key: string,
    value: string | number,
  ) {
    setSettings((current) => {
      if (!current) return current;
      if (section === "plans") {
        const planKey = key.split(".")[0] as "pro" | "custom";
        const field = key.split(".")[1] as "name" | "price_per_site_month";
        return { ...current, plans: { ...current.plans, [planKey]: { ...current.plans[planKey], [field]: value } } };
      }
      if (section === "term_discounts") {
        return { ...current, term_discounts: { ...current.term_discounts, [key]: Number(value) } };
      }
      return { ...current, wallets: { ...current.wallets, [key]: value } };
    });
  }

  const maxDaily = Math.max(1, ...(metrics?.daily.map((item) => item.requests) || []));
  const pendingOrders = metrics?.payment_orders.filter((order) => order.status === "pending") || [];

  return (
    <section className="page-view active">
      <div className="page-heading">
        <div><div className="eyebrow">PLATFORM OPERATIONS</div><h1>Admin dashboard</h1><p>Manage customer accounts, payment review, pricing, and usage analytics.</p></div>
        <select className="admin-range" aria-label="Analytics date range" value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select>
      </div>
      {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError("")}>×</button></div>}
      {notice && <div className="auth-notice" role="status">{notice}</div>}
      {!metrics ? <div className="admin-loading">Loading admin workspace…</div> : <>
        <div className="stats-grid admin-stats">
          <article className="stat-card"><div className="stat-top"><span>Chat requests</span><i className="stat-icon lavender">↗</i></div><div className="stat-number">{metrics.totals.requests.toLocaleString()}</div><div className="stat-description">Requests in selected period</div></article>
          <article className="stat-card"><div className="stat-top"><span>Unique visitors</span><i className="stat-icon green">◉</i></div><div className="stat-number">{metrics.totals.visitors.toLocaleString()}</div><div className="stat-description">Approximate visitor browsers</div></article>
          <article className="stat-card"><div className="stat-top"><span>Successful responses</span><i className="stat-icon peach">✓</i></div><div className="stat-number">{metrics.totals.requests ? `${Math.round(metrics.totals.successful / metrics.totals.requests * 100)}%` : "—"}</div><div className="stat-description">{metrics.totals.unsuccessful} failed or incomplete</div></article>
          <article className="stat-card"><div className="stat-top"><span>Avg. time to first response</span><i className="stat-icon yellow">◷</i></div><div className="stat-number stat-latency">{latency(metrics.totals.avg_response_ms)}</div><div className="stat-description">Average wait until the first answer text</div></article>
        </div>
        <article className="panel admin-chart-panel"><div className="panel-heading"><div><h3>Requests per day</h3><p>Requests and distinct browser visitors, by UTC day</p></div><button className="admin-refresh" onClick={() => void refresh()}>Refresh</button></div>
          {metrics.daily.length ? <div className="daily-chart">{metrics.daily.map((item) => <div className="daily-bar-wrap" key={item.day} title={`${item.day}: ${item.requests} requests, ${item.visitors} visitors`}><span>{item.requests}</span><i style={{ height: `${Math.max(4, item.requests / maxDaily * 100)}%` }} /><small>{item.day.slice(5)}</small></div>)}</div> : <div className="admin-empty">No chat requests have been recorded yet.</div>}
        </article>
        <article className="panel admin-panel"><div className="panel-heading"><div><h3>USDT payment requests</h3><p>Verify the transaction on the selected chain before approving. Review responses are emailed to the customer.</p></div></div>
          {pendingOrders.length ? <div className="admin-table-wrap"><table className="admin-table admin-payments-table"><thead><tr><th>Customer / order</th><th>Payment</th><th>Transaction</th><th>Activated sites / response</th><th>Review</th></tr></thead><tbody>{pendingOrders.map((order) => {
            const draft = reviewDrafts[order.id] || { activated_websites: order.requested_websites, admin_note: "" };
            const minimumSites = order.requested_plan === "custom" ? 2 : 1;
            return <tr key={order.id}><td><b>{order.email}</b><small>{order.requested_plan} · {order.term_months} months · {order.requested_websites} requested site(s)</small><small>{order.created_at} UTC</small></td><td>{order.amount_usd.toFixed(2)} USDT<small>{order.network}</small><code className="admin-wallet">{order.wallet_address}</code></td><td><code className="admin-tx">{order.transaction_hash}</code></td><td><label className="compact-field">Websites<input type="number" min={minimumSites} max={100} value={draft.activated_websites} onChange={(event) => setReviewDrafts((current) => ({ ...current, [order.id]: { ...draft, activated_websites: Number(event.target.value) } }))} /></label><textarea className="review-note" value={draft.admin_note} maxLength={1000} onChange={(event) => setReviewDrafts((current) => ({ ...current, [order.id]: { ...draft, admin_note: event.target.value } }))} placeholder="Response for the customer" /></td><td className="admin-actions"><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, true)}>Approve</button><button disabled={busyId === order.id} onClick={() => void reviewPayment(order, false)}>Reject</button></td></tr>;
          })}</tbody></table></div> : <div className="admin-empty compact">No pending payment requests.</div>}
          {metrics.payment_orders.some((order) => order.status !== "pending") && <div className="payment-order-list">{metrics.payment_orders.filter((order) => order.status !== "pending").slice(0, 10).map((order) => <div className="admin-payment-history" key={order.id}><b>{order.email}</b><span>{order.amount_usd.toFixed(2)} USDT · {order.network} · {order.status}</span>{order.admin_note && <small>{order.admin_note}</small>}</div>)}</div>}
        </article>
        {settings && <article className="panel admin-panel"><div className="panel-heading"><div><h3>Pricing and USDT wallets</h3><p>Set per-website monthly prices, term discounts, and the receiving address for each supported chain.</p></div><button className="button-primary" disabled={savingSettings} onClick={() => void saveSettings()}>{savingSettings ? "Saving…" : "Save billing settings"}</button></div>
          <div className="billing-settings-grid">{(["pro", "custom"] as const).map((plan) => <div className="billing-setting-card" key={plan}><h4>{plan === "pro" ? "Pro plan" : "Custom plan"}</h4><label>Plan label<input value={settings.plans[plan].name} maxLength={30} onChange={(event) => updateSettings("plans", `${plan}.name`, event.target.value)} /></label><label>Price per website / month (USD)<input type="number" min="0" step="0.01" value={settings.plans[plan].price_per_site_month} onChange={(event) => updateSettings("plans", `${plan}.price_per_site_month`, Number(event.target.value))} /></label></div>)}
            <div className="billing-setting-card"><h4>Term discounts</h4>{[1, 2, 6, 12].map((months) => <label key={months}>{months} months discount (%)<input type="number" min="0" max="80" step="1" value={settings.term_discounts[String(months)]} onChange={(event) => updateSettings("term_discounts", String(months), Number(event.target.value))} /></label>)}</div>
            <div className="billing-setting-card wallet-settings"><h4>USDT receiving wallets</h4>{(["TRC20", "ERC20", "BEP20"] as Network[]).map((network) => <label key={network}>{network} address<input value={settings.wallets[network]} maxLength={120} onChange={(event) => updateSettings("wallets", network, event.target.value)} placeholder={`Paste ${network} wallet address`} /></label>)}</div>
          </div>
        </article>}
        <article className="panel admin-panel"><div className="panel-heading"><div><h3>Customer accounts</h3><p>Change plan, website allowance, expiry, and account status. Deletion permanently removes customer data.</p></div></div>
          <div className="admin-table-wrap"><table className="admin-table account-table"><thead><tr><th>Email / traffic</th><th>Plan</th><th>Websites</th><th>Expires (UTC)</th><th>Status</th><th>Actions</th></tr></thead><tbody>{metrics.users.map((user) => {
            const draft = userDrafts[user.id];
            const editable = user.role !== "admin" && Boolean(draft);
            return <tr key={user.id}><td><b>{user.email}</b><small>{user.requests_in_period} requests · {user.visitors} visitors · avg {latency(user.avg_ms)}</small></td><td>{editable ? <select value={draft.plan} onChange={(event) => { const plan = event.target.value as Plan; setUserDrafts((current) => ({ ...current, [user.id]: { ...draft, plan, website_limit: plan === "custom" ? Math.max(2, draft.website_limit) : plan === "free" ? 1 : draft.website_limit } })); }}><option value="free">Free</option><option value="pro">{metrics.plans.pro.name}</option><option value="custom">{metrics.plans.custom.name}</option></select> : <span>{metrics.plans[user.plan].name}</span>}</td><td>{editable ? <input type="number" min={draft.plan === "custom" ? 2 : 1} max={100} value={draft.website_limit} disabled={draft.plan === "free"} onChange={(event) => setUserDrafts((current) => ({ ...current, [user.id]: { ...draft, website_limit: Number(event.target.value) } }))} /> : user.website_limit}</td><td>{editable ? <input type="datetime-local" value={draft.subscription_expires_at} disabled={draft.plan === "free"} onChange={(event) => setUserDrafts((current) => ({ ...current, [user.id]: { ...draft, subscription_expires_at: event.target.value } }))} /> : user.subscription_expires_at || "—"}</td><td>{editable ? <select value={draft.status} onChange={(event) => setUserDrafts((current) => ({ ...current, [user.id]: { ...draft, status: event.target.value as UserDraft["status"] } }))}><option value="active">Active</option><option value="suspended">Suspended</option></select> : <span className={`role-badge ${user.role}`}>{user.role}</span>}</td><td className="admin-actions">{editable ? <><button disabled={busyId === user.id} onClick={() => void saveUser(user)}>Save</button><button className="danger-action" disabled={busyId === user.id} onClick={() => void deleteUser(user)}>Delete</button></> : <span>Protected</span>}</td></tr>;
          })}</tbody></table></div>
        </article>
      </>}
    </section>
  );
}
