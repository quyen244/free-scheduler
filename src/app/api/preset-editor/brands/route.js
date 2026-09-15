import { readdir, readFile } from "fs/promises";
import path from "path";
import { NextResponse } from "next/server";
import { brandAssetPath, brandsDir, mimeType } from "@/lib/preset-editor";

// The editor previews a brand, it never edits one: a visual preset owns
// placement and the brand profile owns the artwork. So this route is read-only
// on purpose, and a wrong logo is fixed in the brand profile rather than here.

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const brandId = searchParams.get("brandId");
  const slot = searchParams.get("slot");
  if (brandId && slot) return serveImage(brandId, slot);
  return listBrands();
}

async function listBrands() {
  let names;
  try {
    names = await readdir(brandsDir());
  } catch {
    return NextResponse.json({ brands: [] });
  }
  const brands = [];
  for (const name of names.filter((file) => file.endsWith(".json"))) {
    try {
      const profile = JSON.parse(await readFile(path.join(brandsDir(), name), "utf-8"));
      brands.push({
        brand_id: profile.brand_id,
        display_name: profile.display_name,
        logo: profile.logo?.file || null,
        logo_opacity: profile.logo?.opacity ?? 1,
        watermark: profile.watermark?.file || null,
        watermark_opacity: profile.watermark?.opacity ?? 1,
      });
    } catch {
      // A malformed profile must not hide the brands that are readable.
    }
  }
  return NextResponse.json({ brands });
}

async function serveImage(brandId, slot) {
  try {
    const profile = JSON.parse(
      await readFile(path.join(brandsDir(), `${brandId}.json`), "utf-8"),
    );
    const file = profile[slot]?.file;
    if (!file) return NextResponse.json({ error: "Brand has no such image" }, { status: 404 });
    const bytes = await readFile(brandAssetPath(brandId, file));
    return new NextResponse(bytes, {
      headers: { "Content-Type": mimeType(file), "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ error: "Brand asset not found" }, { status: 404 });
  }
}
