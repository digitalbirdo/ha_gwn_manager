// README screenshots from a dev instance that runs on the demo server
// (see "Screenshots" in AGENTS.md). Dark mode, English, 2x.
//
//   node scripts/demo/screenshots.mjs <credentials.txt> [out dir] [url]
//
// Writes topology.png, topology-ap-details.png, topology-gateway.png,
// network-map.png, device-ap.png and device-client.png.
import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "node:fs";

const [credentialsFile, outDir = "docs/screenshots", base = "http://127.0.0.1:8123"] = process.argv.slice(2);
if (!credentialsFile) {
  console.error("usage: node screenshots.mjs <credentials.txt> [out dir] [url]");
  process.exit(2);
}
const credentials = readFileSync(credentialsFile, "utf8");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2, colorScheme: "dark", locale: "en-US",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

await page.goto(`${base}/`, { waitUntil: "networkidle" });
await page.fill('input[name="username"]', credentials.match(/user: (\S+)/)[1]);
await page.fill('input[name="password"]', credentials.match(/password: (\S+)/)[1]);
await page.keyboard.press("Enter");
await page.waitForTimeout(4000);
// A changed HTTP port must be confirmed once, otherwise it's reverted.
const confirm = page.getByRole("button", { name: "Confirm" });
if (await confirm.count()) await confirm.first().click();

const devices = await page.evaluate(async () => {
  const hass = document.querySelector("home-assistant").hass;
  const list = await hass.callWS({ type: "config/device_registry/list" });
  return Object.fromEntries(list.map((d) => [d.name, d.id]));
});
const clickIn = (selector) => page.locator(selector).first().evaluate((el) => el.click());

await page.goto(`${base}/gwn-netz/topology`, { waitUntil: "networkidle" });
await page.evaluate(() => { try { localStorage.removeItem("gwn-topology-card:expanded"); } catch (e) { /* ignore */ } });
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(6000);
await page.locator("gwn-topology-card ha-card").screenshot({ path: `${outDir}/topology.png` });

await clickIn("gwn-topology-card .device.ap");
await page.waitForTimeout(4000);
await page.locator("gwn-topology-card .device.ap").first().screenshot({ path: `${outDir}/topology-ap-details.png` });
await clickIn("gwn-topology-card .device.ap");
await page.waitForTimeout(500);
await clickIn("gwn-topology-card .panel.gateway");
await page.waitForTimeout(4000);
await page.locator("gwn-topology-card .panel.gateway").screenshot({ path: `${outDir}/topology-gateway.png` });

await page.goto(`${base}/gwn-netz/map`, { waitUntil: "networkidle" });
await page.waitForTimeout(15000); // let the bubbles settle
await page.locator("gwn-network-card ha-card").screenshot({ path: `${outDir}/network-map.png` });

for (const [name, file] of [["AP Living Room", "device-ap"], ["Phone", "device-client"]]) {
  await page.goto(`${base}/config/devices/device/${devices[name]}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${outDir}/${file}.png` });
}
console.log(errors.length ? `page errors: ${errors.join(" | ")}` : `screenshots in ${outDir}`);
await browser.close();
