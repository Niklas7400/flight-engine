import { useState } from "react";
import type { FormEvent } from "react";
import { supabase } from "./supabase";

export default function Login() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setState("busy");
    setError(null);
    const { error: err } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: { emailRedirectTo: window.location.href },
    });
    if (err) {
      setError(err.message);
      setState("idle");
    } else {
      setState("sent");
    }
  }

  return (
    <main className="container narrow">
      <h1>Flight Engine</h1>
      <form className="card" onSubmit={submit}>
        <label className="field">
          E-Mail
          <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <button type="submit" disabled={state === "busy"}>Anmeldelink senden</button>
        {state === "sent" && <p className="ok">Link gesendet. Bitte E-Mail öffnen und den Link anklicken.</p>}
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  );
}
