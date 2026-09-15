import { mkdir, readFile, writeFile } from "fs/promises";
import path from "path";
import { NextResponse } from "next/server";
import { assetPath, editorRoot, mimeType, validAssetName } from "@/lib/preset-editor";

const MAX_ASSET_BYTES = 500 * 1024 * 1024;

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const presetId = searchParams.get("presetId") || "";
  const asset = searchParams.get("asset") || "";
  try {
    const mattingJobId = searchParams.get("mattingJobId");
    const source = mattingJobId
      ? mattingPreviewPath(presetId, mattingJobId)
      : assetPath(presetId, asset);
    const bytes = await readFile(source);
    return new NextResponse(bytes, { headers: { "Content-Type": mattingJobId ? "image/png" : mimeType(asset), "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "Asset not found" }, { status: 404 });
  }
}

function mattingPreviewPath(presetId, jobId) {
  if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(presetId) || !/^[a-f0-9]{32}$/.test(jobId)) {
    throw new Error("Invalid matting preview path");
  }
  return path.join(editorRoot(), "matting", presetId, jobId, "host-alpha-preview.png");
}

export async function POST(request) {
  const form = await request.formData();
  const presetId = String(form.get("presetId") || "");
  const file = form.get("file");
  if (!(file instanceof File) || !file.name || !validAssetName(file.name)) {
    return NextResponse.json({ error: "Choose an MP4, MOV, PNG, JPG, or WebP file with a plain filename." }, { status: 400 });
  }
  if (file.size > MAX_ASSET_BYTES) {
    return NextResponse.json({ error: "Asset is larger than the 500 MB editor limit." }, { status: 413 });
  }
  try {
    const destination = assetPath(presetId, file.name);
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, Buffer.from(await file.arrayBuffer()));
    return NextResponse.json({ asset: file.name, size: file.size, mime: mimeType(file.name) }, { status: 201 });
  } catch (error) {
    return NextResponse.json({ error: error.message || "Could not save asset" }, { status: 400 });
  }
}
