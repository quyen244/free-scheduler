import path from "path";

const ID = /^[a-z0-9][a-z0-9-]{1,62}$/;
const ASSET = /^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$/;
const TYPES = {
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
};

export function editorRoot() {
  return process.env.PRESET_EDITOR_DATA_DIR || path.join(process.cwd(), "automation", "data", "preset-editor");
}

export function assetPath(presetId, asset) {
  if (!ID.test(presetId)) throw new Error("Invalid preset ID");
  if (!ASSET.test(asset)) throw new Error("Invalid asset filename");
  return path.join(editorRoot(), "assets", presetId, asset);
}

export function validAssetName(value) {
  return ASSET.test(value);
}

export function mimeType(filename) {
  return TYPES[path.extname(filename).toLowerCase()] || "application/octet-stream";
}
