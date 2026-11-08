// Edge Function: loest den GitHub-Workflow track.yml fuer einen Trip aus.
// Secrets: GITHUB_REPO (owner/repo), GITHUB_TOKEN. SUPABASE_URL/SUPABASE_ANON_KEY liefert Supabase.
import { createClient } from "npm:@supabase/supabase-js@2";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ ok: false, error: "Nur POST erlaubt." }, 405);

  const auth = req.headers.get("Authorization");
  if (!auth) return json({ ok: false, error: "Nicht angemeldet." }, 401);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    Deno.env.get("SUPABASE_ANON_KEY") ?? "",
    { global: { headers: { Authorization: auth } } },
  );

  const token = auth.replace(/^Bearer\s+/i, "");
  const { data: userData, error: userErr } = await supabase.auth.getUser(token);
  if (userErr || !userData.user) return json({ ok: false, error: "Nicht angemeldet." }, 401);

  const { data: isAdmin, error: adminErr } = await supabase.rpc("is_admin");
  if (adminErr || isAdmin !== true) return json({ ok: false, error: "Keine Berechtigung." }, 403);

  let trip: unknown;
  let mode: unknown;
  try {
    const body = await req.json();
    trip = body?.trip;
    mode = body?.mode ?? "track";
  } catch {
    return json({ ok: false, error: "Ungueltiger Body." }, 400);
  }
  if (typeof trip !== "string" || !/^[a-z0-9_]{1,64}$/.test(trip)) {
    return json({ ok: false, error: "Ungueltiger Trip-Name." }, 400);
  }
  // track = nur Top-Optionen (~10 SerpApi-Calls), run = volle Suche (~60 Calls)
  if (mode !== "track" && mode !== "run") {
    return json({ ok: false, error: "Ungueltiger Modus." }, 400);
  }

  const repo = Deno.env.get("GITHUB_REPO");
  const ghToken = Deno.env.get("GITHUB_TOKEN");
  if (!repo || !ghToken) return json({ ok: false, error: "Server nicht konfiguriert." }, 500);

  const res = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/track.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${ghToken}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "flight-engine-trigger",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "master", inputs: { trip, mode } }),
  });
  if (res.status !== 204) {
    const text = await res.text();
    return json({ ok: false, error: `GitHub-Fehler ${res.status}`, detail: text.slice(0, 300) }, 502);
  }
  return json({ ok: true, trip });
});
