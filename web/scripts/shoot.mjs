/**
 * Screenshot the landing page at a series of scroll positions.
 *
 *     node scripts/shoot.mjs [url] [outDir]
 *
 * Exists because "it compiles" says nothing about whether a scroll-linked animation
 * actually moves. It drives the installed Chrome through puppeteer-core — no bundled
 * browser download, so it adds a few hundred kilobytes to devDependencies rather than a
 * few hundred megabytes.
 *
 * Console errors and page errors are echoed, which is how a render loop or a failed
 * shader compile gets found without a human watching the tab.
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import puppeteer from "puppeteer-core";

const URL_ = process.argv[2] ?? "http://localhost:5173/";
const OUT = process.argv[3] ?? "shots";
const CHROME =
  process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";

/** Fractions of total scrollable height to capture. */
const STOPS = [0, 0.06, 0.13, 0.22, 0.32, 0.42, 0.55, 0.68, 0.8, 0.92, 1];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: [
    "--window-size=1600,1000",
    // Headless Chrome falls back to SwiftShader without these; the hero needs a GL
    // context or every shot is an empty gradient.
    "--enable-unsafe-swiftshader",
    "--use-gl=angle",
  ],
});

const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });

const problems = [];
page.on("console", (message) => {
  if (message.type() === "error") problems.push(`console: ${message.text().slice(0, 300)}`);
});
page.on("pageerror", (error) => problems.push(`pageerror: ${String(error).slice(0, 300)}`));

console.log(`→ ${URL_}`);
await page.goto(URL_, { waitUntil: "networkidle0", timeout: 60_000 });
await sleep(2500);

mkdirSync(OUT, { recursive: true });

const height = await page.evaluate(
  () => document.documentElement.scrollHeight - window.innerHeight,
);
console.log(`scrollable height: ${height}px`);
if (height < 100) problems.push(`page is not scrollable (${height}px of range)`);

for (const [i, stop] of STOPS.entries()) {
  const target = Math.round(height * stop);
  await page.evaluate((y) => window.scrollTo({ top: y, behavior: "instant" }), target);
  // Long enough for a spring to settle and for whileInView to have fired.
  await sleep(1100);
  const actual = await page.evaluate(() => Math.round(window.scrollY));
  const name = `${String(i).padStart(2, "0")}-${Math.round(stop * 100)}pct.png`;
  await page.screenshot({ path: join(OUT, name) });
  console.log(`  ${name}  (asked ${target}, landed ${actual})`);
}

await browser.close();

if (problems.length) {
  console.log(`\n${problems.length} problem(s):`);
  for (const problem of [...new Set(problems)].slice(0, 12)) console.log(`  ${problem}`);
} else {
  console.log("\nno console or page errors");
}
