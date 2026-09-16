// Dev-only capture harness for the /brands editor. Not part of the app build.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = "http://localhost:3300";
const OUT = "artifacts/brand-ui"; // run from the repo root: node scripts/capture-brand-ui.mjs
const NEW_BRAND = "ui-capture";

const shot = async (page, name) => {
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
  console.log("wrote", name);
};

const pickBrand = async (page, id) => {
  await page.selectOption("header select", id);
  await page.waitForTimeout(1200);
};

const aspect = async (page, label) => {
  await page.getByRole("button", { name: label, exact: true }).click();
  await page.waitForTimeout(900);
};

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on("console", (m) => m.type() === "error" && console.log("[console]", m.text()));
page.on("pageerror", (e) => console.log("[pageerror]", e.message));

// 1-2: a published brand in both ratios.
await page.goto(`${BASE}/brands`, { waitUntil: "networkidle" });
await pickBrand(page, "an-so");
await shot(page, "01-an-so-9x16");
await aspect(page, "16:9");
await shot(page, "02-an-so-16x9");

// 3: selecting a layer opens its numeric property panel.
await aspect(page, "9:16");
await page.locator("li", { hasText: "host" }).filter({ hasText: "Người dẫn" }).first().click();
await shot(page, "03-layer-properties");

// 4: the asset column on its own.
await page.locator("aside").first().screenshot({ path: `${OUT}/04-asset-panel.png` });
console.log("wrote 04-asset-panel");

// 5: the second brand, proving one video can carry several layouts.
await pickBrand(page, "mock-brand");
await shot(page, "05-mock-brand-9x16");

// 6: a brand new brand is a black canvas with no layers.
// Two window.prompt calls in a row: brand id, then display name.
const answers = [NEW_BRAND, "UI capture"];
page.on("dialog", (d) => d.accept(answers.shift() ?? ""));
await page.getByRole("button", { name: /Brand mới/ }).click();
await page.waitForTimeout(2000);
await shot(page, "06-new-brand-black-canvas");

// 7: first layer placed on the empty canvas.
await page.getByRole("button", { name: /Video chính/ }).click();
await shot(page, "07-first-layer-placed");

await browser.close();
