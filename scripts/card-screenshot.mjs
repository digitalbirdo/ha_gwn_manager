// Log in to the dev instance, open a dashboard and screenshot it.
//
//   npm i playwright && npx playwright install chromium-headless-shell
//   node scripts/card-screenshot.mjs <credentials.txt> <dashboard path> [out.png] [url]
//
// Example: node scripts/card-screenshot.mjs ../ha-dev/credentials.txt /gwn-netz/netz map.png
// Prints the number of map nodes/links and any page errors.
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const [credentialsFile, path = "/", out = "screenshot.png", url = "http://127.0.0.1:8123"] =
  process.argv.slice(2);
if (!credentialsFile) {
  console.error("usage: node card-screenshot.mjs <credentials.txt> <dashboard path> [out.png] [url]");
  process.exit(2);
}
const credentials = readFileSync(credentialsFile, "utf8");
const user = credentials.match(/user: (\S+)/)[1];
const password = credentials.match(/password: (\S+)/)[1];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1300, height: 820 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

await page.goto(`${url}/`, { waitUntil: "networkidle" });
await page.fill('input[name="username"]', user);
await page.fill('input[name="password"]', password);
await page.keyboard.press("Enter");
await page.waitForTimeout(3000);
await page.goto(`${url}${path}`, { waitUntil: "networkidle" });
// Give the WebSocket subscription and the force layout time to settle.
await page.waitForTimeout(12000);
await page.screenshot({ path: out });

const info = await page.evaluate(() => {
  const find = (root) => {
    const el = root.querySelector("gwn-network-card");
    if (el) return el;
    for (const n of root.querySelectorAll("*")) {
      if (n.shadowRoot) {
        const found = find(n.shadowRoot);
        if (found) return found;
      }
    }
    return null;
  };
  const card = find(document);
  if (!card) return "no gwn-network-card on this page";
  const root = card.shadowRoot;
  return `nodes=${root.querySelectorAll(".node").length} links=${root.querySelectorAll("line").length}`;
});
console.log(`${out}: ${info}`);
if (errors.length) console.log("page errors:\n" + errors.join("\n"));
await browser.close();
