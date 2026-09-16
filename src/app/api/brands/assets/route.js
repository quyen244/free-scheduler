import { NextResponse } from "next/server";
import { mimeType } from "@/lib/preset-editor";

const service = () => process.env.RENDER_SERVICE_URL || "http://127.0.0.1:8003";

const MAX_ASSET_BYTES = 500 * 1024 * 1024;
const BRAND_ID = /^[a-z0-9][a-z0-9-]{1,62}$/;
const ASSET_ID = /^[a-z0-9][a-z0-9-]{0,31}$/;
const FILE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

/** Stream one stored brand file back for the editor preview. */
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const brand = String(searchParams.get("brandId") || "");
  const file = String(searchParams.get("file") || "");
  if (!BRAND_ID.test(brand) || !FILE.test(file)) {
    return NextResponse.json({ error: "Invalid asset path" }, { status: 400 });
  }
  try {
    const response = await fetch(
      `${service()}/brands/${encodeURIComponent(brand)}/assets/${encodeURIComponent(file)}`,
      { cache: "no-store" },
    );
    if (!response.ok) {
      return NextResponse.json({ error: "Asset not found" }, { status: 404 });
    }
    return new NextResponse(await response.arrayBuffer(), {
      headers: { "Content-Type": mimeType(file), "Cache-Control": "no-store" },
    });
  } catch (error) {
    return NextResponse.json(
      { error: `Render service is unavailable: ${error.message}` },
      { status: 503 },
    );
  }
}

/**
 * Take the browser's multipart upload and hand the bytes on as a raw body.
 *
 * The browser has a file picker and therefore a multipart form; the render
 * service has one endpoint that only ever carries one file and no reason to
 * grow a multipart dependency for it. Translating here costs one copy and
 * keeps that dependency out of the image.
 */
export async function POST(request) {
  const form = await request.formData();
  const brand = String(form.get("brandId") || "");
  const assetId = String(form.get("assetId") || "");
  const role = String(form.get("role") || "other");
  const file = form.get("file");

  if (!BRAND_ID.test(brand)) {
    return NextResponse.json({ error: "Invalid brand ID" }, { status: 400 });
  }
  if (!ASSET_ID.test(assetId)) {
    return NextResponse.json(
      { error: "Mã tài sản chỉ gồm chữ thường, số và dấu gạch ngang." },
      { status: 400 },
    );
  }
  if (!(file instanceof File) || !file.name) {
    return NextResponse.json({ error: "Chọn một tệp để tải lên." }, { status: 400 });
  }
  if (file.size === 0) {
    return NextResponse.json({ error: "Tệp rỗng." }, { status: 400 });
  }
  if (file.size > MAX_ASSET_BYTES) {
    return NextResponse.json(
      { error: "Tệp lớn hơn giới hạn 500 MB của trình soạn thảo." },
      { status: 413 },
    );
  }

  const query = new URLSearchParams({
    asset_id: assetId,
    filename: file.name,
    role,
  });
  try {
    const response = await fetch(
      `${service()}/brands/${encodeURIComponent(brand)}/assets?${query}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: await file.arrayBuffer(),
      },
    );
    return NextResponse.json(await response.json(), { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { error: `Render service is unavailable: ${error.message}` },
      { status: 503 },
    );
  }
}
