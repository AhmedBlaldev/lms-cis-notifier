import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const corsHeaders = {
  "Access-Control-Allow-Origin": "https://ahmedblaldev.github.io",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Content-Type": "application/json",
};

function response(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders });
}

function validEmail(email: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function base64(bytes: Uint8Array) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value: string) {
  const binary = atob(value);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function encryptionKey() {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(serviceRoleKey),
  );
  return crypto.subtle.importKey("raw", digest, "AES-GCM", false, ["encrypt"]);
}

async function encryptPassword(password: string) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      await encryptionKey(),
      new TextEncoder().encode(password),
    ),
  );
  const combined = new Uint8Array(iv.length + encrypted.length);
  combined.set(iv);
  combined.set(encrypted, iv.length);
  return base64(combined);
}

async function hmacToken(email: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(serviceRoleKey),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(email),
  );
  return base64(new Uint8Array(signature))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function supabaseRequest(path: string, init: RequestInit = {}) {
  return fetch(`${supabaseUrl}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: serviceRoleKey,
      Authorization: `Bearer ${serviceRoleKey}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  if (!supabaseUrl || !serviceRoleKey) return response({ error: "Service unavailable" }, 503);

  try {
    const body = await request.json();
    const action = typeof body.action === "string" ? body.action : "";
    const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";

    if (action === "subscribe") {
      const username = typeof body.lms_username === "string" ? body.lms_username.trim() : "";
      const password = typeof body.lms_password === "string" ? body.lms_password : "";
      const prefs = body.notify_prefs && typeof body.notify_prefs === "object"
        ? body.notify_prefs
        : { quiz: true, assign: true, forum: true, resource: true };

      if (!username || username.length > 100 || !password || password.length > 500 ||
        !validEmail(email) || email.length > 254) {
        return response({ error: "Invalid subscription details" }, 400);
      }

      const existing = await supabaseRequest(
        `subscribers?lms_username=eq.${encodeURIComponent(username)}&select=id&limit=1`,
      );
      if (!existing.ok) return response({ error: "Unable to validate subscription" }, 502);
      if ((await existing.json()).length > 0) {
        return response({ error: "This LMS account is already subscribed" }, 409);
      }

      const upstream = await supabaseRequest(
        "subscribers",
        {
          method: "POST",
          headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
          body: JSON.stringify({
            lms_username: username,
            lms_password_ciphertext: await encryptPassword(password),
            email,
            notify_prefs: prefs,
            active: true,
          }),
        },
      );
      if (!upstream.ok) return response({ error: "Unable to save subscription" }, 502);
      return response({ ok: true });
    }

    if (action === "unsubscribe") {
      const token = typeof body.token === "string" ? body.token : "";
      if (!validEmail(email) || email.length > 254 || !token || token !== await hmacToken(email)) {
        return response({ error: "Invalid unsubscribe link" }, 403);
      }

      const upstream = await supabaseRequest(
        `subscribers?email=eq.${encodeURIComponent(email)}`,
        { method: "DELETE", headers: { Prefer: "return=minimal" } },
      );
      if (!upstream.ok) return response({ error: "Unable to unsubscribe" }, 502);
      return response({ ok: true });
    }

    return response({ error: "Unknown action" }, 400);
  } catch {
    return response({ error: "Invalid request" }, 400);
  }
});
