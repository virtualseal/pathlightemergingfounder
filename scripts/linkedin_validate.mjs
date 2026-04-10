#!/usr/bin/env node
import fs from "node:fs";
import { chromium } from "playwright";

const args = parseArgs(process.argv.slice(2));
const input = args.input || "data/candidates.json";
const output = args.output || "data/linkedin-validation.json";
const delayMs = Number(args.delayMs || 12000);
const timeoutMs = Number(args.timeoutMs || 25000);
const maxProfiles = Number(args.maxProfiles || 10);
const headless = args.headless === "true";
const profileDir = args.profileDir || ".linkedin-browser-profile";

if (!fs.existsSync(input)) {
  console.error(`Input file not found: ${input}`);
  process.exit(1);
}

const candidates = JSON.parse(fs.readFileSync(input, "utf8")).slice(0, maxProfiles);
fs.mkdirSync("data", { recursive: true });

const context = await chromium.launchPersistentContext(profileDir, {
  headless,
  viewport: { width: 1280, height: 900 },
});
const page = await context.newPage();
const validations = [];

for (const candidate of candidates) {
  const url = candidate.linkedin_url || candidate.linkedinUrl || candidate.url;
  if (!url) continue;

  console.error(`Opening ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs }).catch((error) => {
    validations.push({ url, verdict: "Needs Review", reasons: [`Navigation failed: ${error.message}`] });
  });
  await page.waitForTimeout(delayMs);

  const currentUrl = page.url();
  const bodyText = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
  if (isBlockedOrLoggedOut(currentUrl, bodyText)) {
    validations.push({
      url,
      verdict: "Needs Review",
      reasons: ["LinkedIn login, checkpoint, or bot-protection page detected. Stopping validation."],
    });
    break;
  }

  validations.push(validateProfile(url, bodyText));
  fs.writeFileSync(output, JSON.stringify(validations, null, 2));
}

await context.close();
console.error(`Wrote ${validations.length} validations to ${output}`);

function parseArgs(values) {
  const parsed = {};
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (!value.startsWith("--")) continue;
    const key = value.slice(2);
    const next = values[i + 1];
    if (!next || next.startsWith("--")) {
      parsed[key] = "true";
    } else {
      parsed[key] = next;
      i += 1;
    }
  }
  return parsed;
}

function isBlockedOrLoggedOut(url, text) {
  const lower = `${url}\n${text}`.toLowerCase();
  return [
    "/login",
    "checkpoint",
    "security verification",
    "verify your identity",
    "unusual activity",
    "authwall",
    "sign in",
  ].some((term) => lower.includes(term));
}

function validateProfile(url, text) {
  const header = text.slice(0, 1800).toLowerCase();
  const reasons = [];

  if (!isUSBased(header)) reasons.push("No clear US location in profile header.");
  if (hasCurrentFounderRole(header) && !hasEarlyStealthSignal(header)) {
    reasons.push("Obvious current founder at a named company.");
  }
  if (hasFreelanceOrFractionalRole(header)) {
    reasons.push("Freelance/fractional/consulting profile, not emerging founder signal.");
  }
  if (hasCurrentSmallStartupEmployeeSignal(header)) {
    reasons.push("Currently working at a small startup, not pre-founder transition.");
  }
  if (hasShortCurrentTenure(header) && !hasStrongTransitionSignal(header)) {
    reasons.push("Current tenure appears too short without strong transition signal.");
  }
  if (!hasBuilderFunction(header)) {
    reasons.push("No strong product/engineering/data/design builder signal in visible header.");
  }
  if (!hasStrongTransitionSignal(header) && !hasVestingSignal(header)) {
    reasons.push("No clear transition or vesting-window signal.");
  }

  return {
    url,
    verdict: reasons.length ? "Reject" : "Pass",
    reasons,
    checked_at: new Date().toISOString(),
  };
}

function isUSBased(text) {
  return [
    "united states",
    "san francisco",
    "bay area",
    "new york",
    "seattle",
    "los angeles",
    "boston",
    "austin",
    "brooklyn",
    "california",
    "texas",
  ].some((term) => text.includes(term));
}

function hasCurrentFounderRole(text) {
  return /(founder|co-founder|co founder|ceo).{0,80}(present|current)/i.test(text);
}

function hasEarlyStealthSignal(text) {
  return /(stealth|building something new|something new|exploring)/i.test(text);
}

function hasFreelanceOrFractionalRole(text) {
  return /(freelance|fractional|consultant|consulting|independent design engineer|agency)/i.test(text);
}

function hasCurrentSmallStartupEmployeeSignal(text) {
  return /(current|present)[\s\S]{0,600}(1-10 employees|11-50 employees|startup)/i.test(text)
    && !/(founder|co-founder|co founder)/i.test(text.slice(0, 700));
}

function hasShortCurrentTenure(text) {
  const match = text.match(/present\s*[·•]\s*(?:(\d+)\s*years?)?\s*(?:and\s*)?(?:(\d+)\s*months?)?/i);
  if (!match) return false;
  const years = Number(match[1] || 0);
  const months = Number(match[2] || 0);
  return years * 12 + months < 36;
}

function hasBuilderFunction(text) {
  return /(engineer|engineering|software|product|data|machine learning|ml|ai|design)/i.test(text);
}

function hasStrongTransitionSignal(text) {
  return /(building something new|something new|stealth|exploring|ex-[a-z])/i.test(text.slice(0, 700));
}

function hasVestingSignal(text) {
  return /(3 years|4 years|5 years|3 yrs|4 yrs|5 yrs)/i.test(text.slice(0, 1800));
}
