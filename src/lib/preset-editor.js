import path from "path";

const ID = /^[a-z0-9][a-z0-9-]{1,62}$/;
const BRAND_ID = /^[A-Za-z0-9_-]{1,64}$/;
const ASSET = /^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$/;
const BRAND_ASSET = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const TYPES = {
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
};

// The seven confirmed editor colours, mirroring PALETTE in
// automation/render-service/visual_preset.py. A preset stores the name, so a
// swatch here and a drawtext colour there cannot drift apart.
export const PALETTE = {
  white: "#FFFFFF",
  black: "#000000",
  red: "#FF3B30",
  yellow: "#FFD60A",
  green: "#34C759",
  blue: "#0A84FF",
  purple: "#AF52DE",
};

export function dataRoot() {
  return process.env.RENDER_DATA_DIR || path.join(process.cwd(), "automation", "data");
}

export function editorRoot() {
  return process.env.PRESET_EDITOR_DATA_DIR || path.join(dataRoot(), "preset-editor");
}

export function assetPath(presetId, asset) {
  if (!ID.test(presetId)) throw new Error("Invalid preset ID");
  if (!ASSET.test(asset)) throw new Error("Invalid asset filename");
  return path.join(editorRoot(), "assets", presetId, asset);
}

export function brandsDir() {
  return path.join(dataRoot(), "presets", "brands");
}

export function brandAssetPath(brandId, asset) {
  if (!BRAND_ID.test(brandId)) throw new Error("Invalid brand ID");
  if (!BRAND_ASSET.test(asset)) throw new Error("Invalid brand asset filename");
  return path.join(dataRoot(), "presets", "brand-assets", brandId, asset);
}

export function validAssetName(value) {
  return ASSET.test(value);
}

export function mimeType(filename) {
  return TYPES[path.extname(filename).toLowerCase()] || "application/octet-stream";
}
