import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { App } from "@slack/bolt";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const envPath = path.join(root, ".env.local");
const sentPath = path.join(root, "data", "slack-candidates.json");

loadEnv(envPath);

const required = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "NOTION_TOKEN", "NOTION_DATABASE_ID"];
const missing = required.filter((key) => !process.env[key]);
if (missing.length) {
  throw new Error(`missing env values: ${missing.join(", ")}`);
}

const approveEmoji = (process.env.SLACK_APPROVE_EMOJI || "white_check_mark")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const rejectEmoji = (process.env.SLACK_REJECT_EMOJI || "red_circle,red-x,x,red_x,negative_squared_cross_mark")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const approveStatus = process.env.NOTION_APPROVE_STATUS || "Approved";
const rejectStatus = process.env.NOTION_REJECT_STATUS || "Rejected";

const app = new App({
  token: process.env.SLACK_BOT_TOKEN,
  appToken: process.env.SLACK_APP_TOKEN,
  socketMode: true,
});

app.event("reaction_added", async ({ event, client }) => {
  const item = event.item || {};
  if (item.type !== "message" || !item.channel || !item.ts) {
    return;
  }

  const status = statusForReaction(event.reaction);
  if (!status) {
    return;
  }

  const sent = loadSent();
  const record = sent.messages?.[`${item.channel}:${item.ts}`];
  if (!record?.page_id) {
    console.log(`No Notion mapping for ${item.channel}:${item.ts}`);
    return;
  }

  const result = spawnSync(
    "python3",
    ["founder_scan.py", "--set-page-status", record.page_id, status],
    {
      cwd: root,
      env: process.env,
      encoding: "utf8",
    },
  );

  if (result.status !== 0) {
    console.error(result.stderr || result.stdout);
    await addReaction(client, item.channel, item.ts, "warning");
    return;
  }

  console.log((result.stdout || "").trim());
  await addReaction(client, item.channel, item.ts, status === approveStatus ? "white_check_mark" : "red_circle");
});

await app.start();
console.log("Listening for Slack candidate reactions with Socket Mode.");

function statusForReaction(reaction) {
  if (approveEmoji.includes(reaction)) {
    return approveStatus;
  }
  if (rejectEmoji.includes(reaction)) {
    return rejectStatus;
  }
  return "";
}

function loadSent() {
  if (!fs.existsSync(sentPath)) {
    return { pages: {}, messages: {} };
  }
  return JSON.parse(fs.readFileSync(sentPath, "utf8"));
}

async function addReaction(client, channel, timestamp, name) {
  try {
    await client.reactions.add({ channel, timestamp, name });
  } catch (error) {
    if (error?.data?.error !== "already_reacted") {
      console.error(`Could not add ${name} reaction: ${error?.data?.error || error.message}`);
    }
  }
}

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    return;
  }
  for (const rawLine of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, "");
    if (key && !process.env[key]) {
      process.env[key] = value;
    }
  }
}
