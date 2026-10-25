import { FormEvent, useEffect, useState } from "react";
import "./landing.css";

type AuthUser = { id: string; email: string; role: "user" | "admin"; plan: "free" | "pro" | "custom" };

export function AuthScreen({ onAuthenticated }: { onAuthenticated: (user: AuthUser, csrf: string) => void }) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showLogin, setShowLogin] = useState(window.location.hash === "#signin");

  useEffect(() => {
    const update = () => setShowLogin(window.location.hash === "#signin");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  function openLogin() {
    window.history.replaceState(null, "", "#signin");
    setShowLogin(true);
  }

  if (!showLogin) {
    return (
      <main className="marketing">
        <header className="marketing-nav">
          <a className="brand" href="/"><span className="brand-mark">✳</span><span>nimbus<span className="brand-period">.</span></span></a>
          <nav><a href="#how-it-works">How it works</a><a href="#features">Features</a><a href="#pricing">Plans</a></nav>
          <button className="marketing-signin" onClick={openLogin}>Sign in</button>
        </header>
        <section className="marketing-hero">
          <div className="marketing-copy">
            <div className="marketing-kicker"><i /> AI SUPPORT, GROUNDED IN YOUR BUSINESS</div>
            <h1>Make every website visit feel like a <span>conversation.</span></h1>
            <p>Give your customers quick, helpful answers with an AI chat widget that knows your website, your services, and your business.</p>
            <div className="marketing-actions"><button onClick={openLogin}>Create your free workspace <span>→</span></button><a href="#how-it-works">See how it works <span>↓</span></a></div>
            <div className="marketing-proof"><span>✳</span> Easy to install <i /> No password required <i /> Starts with your website content</div>
          </div>
          <div className="marketing-visual" aria-label="Preview of the Nimbus customer support chat widget">
            <div className="marketing-browser"><div className="browser-chrome"><i /><i /><i /><span>yourbusiness.com</span></div><div className="marketing-webpage"><small>YOUR BUSINESS, MADE SIMPLE</small><h2>Everything you need,<br />all in one place.</h2><p>Thoughtful service for the things that matter.</p><span className="marketing-cta">Explore our services</span><span className="marketing-orb orb-one" /><span className="marketing-orb orb-two" /></div></div>
            <div className="marketing-chat"><div className="marketing-chat-head"><span>✳</span><div><b>Your assistant</b><small><i /> Online and ready to help</small></div><span className="marketing-menu">···</span></div><div className="marketing-chat-body"><small>TODAY</small><div className="marketing-answer">Hi there! 👋 What can I help you find?</div><div className="marketing-question">Do you offer consultations?</div><div className="marketing-answer">Yes! We offer an initial consultation to learn about your needs.</div></div><div className="marketing-chat-input">Ask us anything... <span>➤</span></div></div>
            <div className="marketing-float">✳</div>
          </div>
        </section>
        <section className="marketing-trust"><span>BUILT TO FIT YOUR BUSINESS</span><div><b>Simple setup</b><i /><b>Answers based on your content</b><i /><b>Works on your website</b></div></section>
        <section className="marketing-section" id="how-it-works">
          <div className="marketing-section-heading"><div className="marketing-kicker">UP AND RUNNING IN MINUTES</div><h2>From your website to helpful answers.</h2><p>Everything you need to launch an AI assistant for your customers.</p></div>
          <div className="marketing-steps"><article><span>01</span><div className="marketing-step-icon">◎</div><h3>Add your website</h3><p>Import public website content and add the details your assistant should know.</p></article><article><span>02</span><div className="marketing-step-icon">✳</div><h3>Make it yours</h3><p>Preview your assistant and test responses before your customers see it.</p></article><article><span>03</span><div className="marketing-step-icon">‹/›</div><h3>Install one snippet</h3><p>Copy a small embed code and add the floating chat button to your website.</p></article></div>
        </section>
        <section className="marketing-features" id="features"><div><div className="marketing-kicker">A BETTER CUSTOMER EXPERIENCE</div><h2>Helpful, on-brand support.<br />Right where customers need it.</h2><p>Nimbus brings your business information into a friendly chat experience, with thoughtful streaming replies and clear reporting for your team.</p><button onClick={openLogin}>Get started <span>→</span></button></div><ul><li><i>✓</i><span><b>Website-aware answers</b><small>Ground responses in your approved business information.</small></span></li><li><i>✓</i><span><b>Live, natural replies</b><small>Give visitors a smooth chat experience with animated responses.</small></span></li><li><i>✓</i><span><b>Clear usage insights</b><small>Understand request volume, success rate, and response time.</small></span></li><li><i>✓</i><span><b>Keep control</b><small>Manage workspaces and billing from one dashboard.</small></span></li></ul></section>
        <section className="marketing-pricing" id="pricing"><div className="marketing-section-heading"><div className="marketing-kicker">PLANS THAT FIT YOUR NEEDS</div><h2>Start free. Scale when you’re ready.</h2><p>Choose a free workspace, then explore paid options configured by the Nimbus administrator.</p></div><div className="marketing-plan-cards"><article><span>START HERE</span><h3>Free</h3><p>Try your assistant on one website and see how Nimbus fits your workflow.</p><button onClick={openLogin}>Create a free workspace <span>→</span></button></article><article className="featured"><span>GROW WITH NIMBUS</span><h3>Flexible plans</h3><p>Paid pricing, website allowances, and term discounts are configured for your account.</p><button onClick={openLogin}>View plans after sign in <span>→</span></button></article></div></section>
        <footer className="marketing-footer"><a className="brand" href="/"><span className="brand-mark">✳</span><span>nimbus<span className="brand-period">.</span></span></a><span>Useful AI support for the websites you build.</span><button onClick={openLogin}>Sign in to your workspace</button></footer>
      </main>
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const endpoint = step === "email" ? "/api/auth/request-otp" : "/api/auth/verify-otp";
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(step === "email" ? { email } : { email, code }),
      });
      const result = (await response.json()) as {
        error?: string;
        message?: string;
        csrf_token?: string;
        user?: AuthUser;
      };
      if (!response.ok) throw new Error(result.error || "Sign-in failed. Please try again.");
      if (step === "email") {
        setStep("code");
        setNotice(result.message || "Check your email for a sign-in code.");
      } else if (result.user && result.csrf_token) {
        onAuthenticated(result.user, result.csrf_token);
      } else {
        throw new Error("The sign-in response was incomplete. Please request a new code.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign-in failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-card">
        <a className="brand auth-brand" href="/" onClick={(event) => { event.preventDefault(); window.history.replaceState(null, "", window.location.pathname); setShowLogin(false); }}><span className="brand-mark">✳</span><span>nimbus<span className="brand-period">.</span></span></a>
        <div className="eyebrow">YOUR AI SUPPORT WORKSPACE</div>
        <h1>{step === "email" ? "Sign in to Nimbus" : "Check your email"}</h1>
        <p className="auth-subtitle">{step === "email" ? "We’ll email you a one-time code. No password needed." : `Enter the six-digit code sent to ${email}.`}</p>
        <form onSubmit={(event) => void submit(event)} className="auth-form">
          {step === "email" ? (
            <>
              <label htmlFor="auth-email">Email address</label>
              <input id="auth-email" type="email" autoComplete="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" />
            </>
          ) : (
            <>
              <label htmlFor="auth-code">One-time code</label>
              <input id="auth-code" inputMode="numeric" autoComplete="one-time-code" autoFocus required maxLength={6} pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="000000" />
            </>
          )}
          {error && <div className="auth-error" role="alert">{error}</div>}
          {notice && <div className="auth-notice" role="status">{notice}</div>}
          <button className="button-primary auth-submit" disabled={busy}>{busy ? "Please wait..." : step === "email" ? "Email me a sign-in code" : "Verify and sign in"}</button>
        </form>
        {step === "code" && <button className="auth-back" onClick={() => { setStep("email"); setCode(""); setError(""); setNotice(""); }}>Use a different email or request another code</button>}
        <p className="auth-footnote">New here? Enter your email to create an account. Your email code verifies your address.</p>
      </section>
    </main>
  );
}

export type { AuthUser };
