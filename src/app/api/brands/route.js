import { NextResponse } from "next/server";

// The render service owns brand storage and validation. This route only
// forwards, so a rule such as "a host needs an alpha mask" is stated once, in
// the place that also has to hold when n8n calls the same API without a browser.
const service = () => process.env.RENDER_SERVICE_URL || "http://127.0.0.1:8003";

const BRAND_ID = /^[a-z0-9][a-z0-9-]{1,62}$/;

async function proxy(path, init) {
  let response;
  try {
    response = await fetch(`${service()}${path}`, { cache: "no-store", ...init });
  } catch (error) {
    // Only a failed connection is "unavailable". An answer that arrived is the
    // service's answer, however it is shaped.
    return NextResponse.json(
      { error: `Render service is unavailable: ${error.message}` },
      { status: 503 },
    );
  }
  const body = await response.text();
  try {
    return NextResponse.json(JSON.parse(body), { status: response.status });
  } catch {
    // FastAPI answers an unhandled exception with the plain text "Internal
    // Server Error". Parsing that as JSON used to fail and be reported as a
    // dead service, which sent the reader looking for the wrong problem — the
    // real one was in the render service's log the whole time.
    return NextResponse.json(
      { error: `Render service error ${response.status}: ${body.slice(0, 300) || "(empty response)"}` },
      { status: response.status === 200 ? 502 : response.status },
    );
  }
}

const json = (payload) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

function brandId(searchParams) {
  const value = String(searchParams.get("brandId") || "");
  if (!BRAND_ID.test(value)) throw new Error("Invalid brand ID");
  return encodeURIComponent(value);
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const action = searchParams.get("action");
  try {
    if (action === "brands") return proxy("/brands");
    if (action === "draft") return proxy(`/brands/${brandId(searchParams)}/draft`);
    if (action === "latest") return proxy(`/brands/${brandId(searchParams)}/latest`);
    if (action === "published") {
      // "latest" is a legal spelling of a revision here; the render service
      // resolves it and is the only place that may.
      const revision = encodeURIComponent(searchParams.get("revision") || "");
      return proxy(`/brands/${brandId(searchParams)}/revisions/${revision}`);
    }
    if (action === "render-config") {
      const revision = encodeURIComponent(searchParams.get("revision") || "");
      const aspect = encodeURIComponent(searchParams.get("aspect") || "vertical");
      return proxy(`/brands/${brandId(searchParams)}/render-config/${revision}/${aspect}`);
    }
    if (action === "rvm") return proxy("/preset-editor/rvm");
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 400 });
  }
  return NextResponse.json({ error: "Unknown brand action" }, { status: 400 });
}

export async function POST(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Expected JSON" }, { status: 400 });
  }
  const { action, ...payload } = body;
  if (action === "create") return proxy("/brands", json(payload));

  const id = String(payload.brand_id || "");
  if (!BRAND_ID.test(id)) {
    return NextResponse.json({ error: "Invalid brand ID" }, { status: 400 });
  }
  const encoded = encodeURIComponent(id);
  if (action === "save-draft") return proxy(`/brands/${encoded}/draft`, json(payload));
  if (action === "publish") return proxy(`/brands/${encoded}/publish`, json(payload));
  if (action === "matting") {
    return proxy(
      `/brands/${encoded}/matting`,
      json({ file: payload.file, corrections: payload.corrections || [] }),
    );
  }
  return NextResponse.json({ error: "Unknown brand action" }, { status: 400 });
}
