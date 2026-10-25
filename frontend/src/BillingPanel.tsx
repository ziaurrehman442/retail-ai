import { useEffect, useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import "./billing.css";

type Plan = "free" | "pro" | "custom";
type Network = "TRC20" | "ERC20" | "BEP20";
type Billing = {
  plan: Plan;
  usage_this_month: number;
  websites: number;
  website_limit: number;
  subscription_expires_at: string | null;
  payments_enabled: boolean;
  plans: Record<Plan, { name: string; price_per_site_month?: number; monthly_requests: number | null }>;
  term_discounts: Record<string, number>;
  wallets: Record<Network, string>;
};
type PaymentOrder = {
  id: string;
  requested_plan: Exclude<Plan, "free">;
  term_months: number;
  requested_websites: number;
  amount_usd: number;
  network: Network;
  wallet_address: string;
  transaction_hash: string;
  status: "pending" | "approved" | "rejected";
  admin_note: string;
  activated_websites: number | null;
  created_at: string;
  reviewed_at: string | null;
};

const terms = [1, 2, 6, 12] as const;
const networks: Network[] = ["TRC20", "ERC20", "BEP20"];

export function BillingPanel({ csrfToken }: { csrfToken: string }) {
  const [billing, setBilling] = useState<Billing | null>(null);
  const [orders, setOrders] = useState<PaymentOrder[]>([]);
  const [plan, setPlan] = useState<Exclude<Plan, "free">>("pro");
  const [term, setTerm] = useState<number>(1);
  const [websites, setWebsites] = useState(1);
  const [network, setNetwork] = useState<Network>("TRC20");
  const [transactionHash, setTransactionHash] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  function selectPlan(nextPlan: Exclude<Plan, "free">) {
    setPlan(nextPlan);
    if (nextPlan === "custom" && websites < 2) setWebsites(2);
  }

  async function loadData() {
    const [billingResponse, ordersResponse] = await Promise.all([
      fetch("/api/billing"),
      fetch("/api/billing/orders"),
    ]);
    const billingResult = (await billingResponse.json()) as Billing & { error?: string };
    const ordersResult = (await ordersResponse.json()) as PaymentOrder[] & { error?: string };
    if (!billingResponse.ok) throw new Error(billingResult.error || "Could not load plan details.");
    if (!ordersResponse.ok) throw new Error(ordersResult.error || "Could not load payment requests.");
    setBilling(billingResult);
    setOrders(ordersResult);
    const available = networks.find((item) => Boolean(billingResult.wallets[item]));
    if (available) setNetwork(available);
  }

  useEffect(() => {
    void loadData().catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Could not load billing details.");
    });
  }, []);

  const amount = useMemo(() => {
    if (!billing) return 0;
    const price = billing.plans[plan]?.price_per_site_month || 0;
    const discount = billing.term_discounts[String(term)] || 0;
    return Math.round(price * websites * term * (100 - discount)) / 100;
  }, [billing, plan, term, websites]);

  async function submitPayment() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/billing/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({
          plan,
          term_months: term,
          websites,
          network,
          transaction_hash: transactionHash.trim(),
        }),
      });
      const result = (await response.json()) as { error?: string; warning?: string | null };
      if (!response.ok) throw new Error(result.error || "Could not submit your payment request.");
      setTransactionHash("");
      setNotice(result.warning || "Payment request sent. The administrator will review the transaction and update your dashboard.");
      await loadData();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not submit your payment request.");
    } finally {
      setBusy(false);
    }
  }

  if (!billing) {
    return <article className="form-card billing-panel"><div className="form-title"><div><h3>Plans and usage</h3><p>Loading your plan and payment settings…</p></div></div>{error && <div className="auth-error">{error}</div>}</article>;
  }

  const activeNetworks = networks.filter((item) => Boolean(billing.wallets[item]));
  const wallet = billing.wallets[network];
  const quota = billing.plans[billing.plan].monthly_requests;
  const minimumWebsites = plan === "custom" ? 2 : 1;
  const usagePercent = quota ? Math.min(100, billing.usage_this_month / quota * 100) : 0;

  return (
    <article className="form-card billing-panel">
      <div className="form-title"><span className="form-icon lilac">◇</span><div><h3>Plans and usage</h3><p>Choose a plan, term, and website count. USDT payments are manually reviewed.</p></div></div>
      {error && <div className="auth-error" role="alert">{error}</div>}
      {notice && <div className="auth-notice" role="status">{notice}</div>}
      <div className="usage-summary">
        <div><b>This month&apos;s requests</b><span>{billing.usage_this_month.toLocaleString()} {quota === null ? "· unlimited" : `/ ${quota}`}</span></div>
        <div className="quota-track"><i style={{ width: quota ? `${usagePercent}%` : "100%" }} /></div>
        <small>{billing.websites} of {billing.website_limit} website slot(s) active{billing.subscription_expires_at ? ` · subscription ends ${billing.subscription_expires_at.slice(0, 10)} UTC` : ""}.</small>
      </div>
      <div className="plan-cards">
        {(Object.entries(billing.plans) as [Plan, Billing["plans"][Plan]][]).map(([id, item]) => (
          <section className={`plan-option ${billing.plan === id ? "current" : ""}`} key={id}>
            <div className="plan-option-heading"><b>{item.name}</b>{billing.plan === id && <span>YOUR PLAN</span>}</div>
            <div className="plan-price">{id === "free" ? "$0" : `$${item.price_per_site_month}`}<small>{id === "free" ? " / month" : " / website / month"}</small></div>
            <ul><li>{item.monthly_requests === null ? "Unlimited chat requests" : `${item.monthly_requests} chat requests / month`}</li><li>{id === "free" ? "1 website widget" : id === "custom" ? "Minimum 2 websites" : "Choose website allowance"}</li></ul>
            {id !== "free" && <button className={plan === id ? "selected-plan" : ""} onClick={() => selectPlan(id)}>Select {item.name}</button>}
          </section>
        ))}
      </div>
      {activeNetworks.length ? <section className="payment-checkout">
        <div className="panel-heading"><div><h3>Pay with USDT</h3><p>Transfers are not automatic. Verify the network and amount before sending.</p></div><span className="payment-manual-tag">MANUAL REVIEW</span></div>
        <div className="checkout-fields">
          <label>Subscription plan<select value={plan} onChange={(event) => selectPlan(event.target.value as Exclude<Plan, "free">)}><option value="pro">{billing.plans.pro.name}</option><option value="custom">{billing.plans.custom.name}</option></select></label>
          <label>Subscription length<select value={term} onChange={(event) => setTerm(Number(event.target.value))}>{terms.map((months) => <option value={months} key={months}>{months} {months === 1 ? "month" : "months"}{billing.term_discounts[String(months)] ? ` · ${billing.term_discounts[String(months)]}% off` : ""}</option>)}</select></label>
          <label>Websites<select value={websites} onChange={(event) => setWebsites(Number(event.target.value))}>{Array.from({ length: 101 - minimumWebsites }, (_, index) => index + minimumWebsites).map((count) => <option key={count} value={count}>{count} website{count === 1 ? "" : "s"}</option>)}</select></label>
          <label>USDT network<select value={network} onChange={(event) => setNetwork(event.target.value as Network)}>{activeNetworks.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        </div>
        <div className="payment-total"><span>Amount due</span><b>{amount.toFixed(2)} USDT</b><small>{billing.term_discounts[String(term)] || 0}% term discount · USDT equivalent of USD pricing</small></div>
        <div className="wallet-payment">
          <div className="wallet-qr">{wallet ? <QRCodeSVG value={wallet} size={150} level="M" includeMargin /> : <span>Wallet not configured</span>}<small>{network} · QR encodes the wallet address</small></div>
          <div className="wallet-details">
            <strong>Send exactly {amount.toFixed(2)} USDT on {network}</strong>
            <p>Only send USDT using the selected network. Sending through another chain may permanently lose the funds. The QR code contains the wallet address; confirm the amount in your wallet app.</p>
            <label>Receiving wallet address</label><code className="wallet-address">{wallet}</code>
            <label htmlFor="payment-tx">Transaction hash (after sending)</label><input id="payment-tx" value={transactionHash} onChange={(event) => setTransactionHash(event.target.value)} maxLength={200} placeholder="Paste the blockchain transaction hash" />
            <button className="button-primary" disabled={busy || !transactionHash.trim()} onClick={() => void submitPayment()}>{busy ? "Submitting…" : "Submit payment for review"}</button>
          </div>
        </div>
      </section> : <div className="payment-unavailable">USDT checkout is not available yet. The administrator must configure at least one wallet address.</div>}
      <p className="billing-disclaimer">A payment request does not activate service automatically. The administrator checks the transaction, can adjust the active website allowance, and sends their response to your dashboard and email.</p>
      <section className="payment-history"><div className="panel-heading"><div><h3>Payment requests</h3><p>Review status and administrator responses.</p></div><button className="admin-refresh" onClick={() => void loadData().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not refresh payment requests."))}>Refresh</button></div>
        {orders.length ? <div className="payment-order-list">{orders.map((order) => <article className="payment-order" key={order.id}><div className="payment-order-top"><b>{billing.plans[order.requested_plan].name} · {order.amount_usd.toFixed(2)} USDT</b><span className={`payment-status ${order.status}`}>{order.status}</span></div><p>{order.term_months} months · {order.requested_websites} requested website(s) · {order.network} · {order.created_at} UTC</p><code>{order.transaction_hash}</code>{order.activated_websites !== null && order.status === "approved" && <small>{order.activated_websites} website(s) activated</small>}{order.admin_note && <div className="admin-response"><b>Administrator response</b><span>{order.admin_note}</span></div>}</article>)}</div> : <div className="admin-empty compact">No payment requests yet.</div>}
      </section>
    </article>
  );
}
