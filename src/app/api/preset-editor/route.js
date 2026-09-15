import { NextResponse } from "next/server";

const service = () => process.env.RENDER_SERVICE_URL || "http://127.0.0.1:8003";

async function proxy(path, init) {
  try {
    const response = await fetch(`${service()}${path}`, { cache: "no-store", ...init });
    const payload = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { error: `Render service is unavailable: ${error.message}` },
      { status: 503 },
    );
  }
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const action = searchParams.get("action");
  if (action === "presets") return proxy("/preset-editor/presets");
  if (action === "rvm") return proxy("/preset-editor/rvm");
  if (action === "draft") return proxy(`/preset-editor/presets/${encodeURIComponent(searchParams.get("presetId") || "")}/draft`);
  if (action === "published") {
    const presetId = encodeURIComponent(searchParams.get("presetId") || "");
    const revision = encodeURIComponent(searchParams.get("revision") || "");
    return proxy(`/preset-editor/presets/${presetId}/revisions/${revision}`);
  }
  if (action === "matting") return proxy(`/preset-editor/matting/${encodeURIComponent(searchParams.get("jobId") || "")}`);
  return NextResponse.json({ error: "Unknown editor action" }, { status: 400 });
}

export async function POST(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Expected JSON" }, { status: 400 });
  }
  const { action, ...payload } = body;
  if (action === "save-draft") return proxy("/preset-editor/drafts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (action === "publish") return proxy("/preset-editor/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (action === "matting") return proxy("/preset-editor/matting", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  return NextResponse.json({ error: "Unknown editor action" }, { status: 400 });
}
