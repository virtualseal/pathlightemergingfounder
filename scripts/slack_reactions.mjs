import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { App } from "@slack/bolt";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const envPath = path.join(root, ".env.local");
const sentPath = path.join(root, "data", "slack-candidates.json");
const envAliases = {
  "mercedes-codex-socket-mode": "SLACK_APP_TOKEN",
  "mercedes-codex-bot-user-token": "SLACK_BOT_TOKEN",
  "alerts-new-founders-channel-id": "SLACK_CHANNEL_ID",
};

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
const watchlistEmoji = (process.env.SLACK_WATCHLIST_EMOJI || "eyes")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const approveStatus = process.env.NOTION_APPROVE_STATUS || "Approved";
const rejectStatus = process.env.NOTION_REJECT_STATUS || "Rejected";
const watchlistStatus = process.env.NOTION_WATCHLIST_STATUS || "Watchlist";
const rejectionReasonActionId = "rejection_reason_selected";
const rejectionReasons = [
  "Already a founder",
  "Not founder-like",
  "Weak role/function",
  "Too senior / established",
  "Too junior",
  "Wrong geography",
  "Not enough evidence",
  "Bad source/result",
  "Duplicate",
  "Other",
];

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

  const result = runFounderScan(["--set-page-status", record.page_id, status]);

  if (result.status !== 0) {
    console.error(result.stderr || result.stdout);
    await addReaction(client, item.channel, item.ts, "warning");
    return;
  }

  console.log((result.stdout || "").trim());
  const confirmationEmoji = status === approveStatus ? "white_check_mark" : status === watchlistStatus ? "eyes" : "red_circle";
  await addReaction(client, item.channel, item.ts, confirmationEmoji);
  if (status === rejectStatus) {
    await postRejectionReasonPicker(client, item.channel, item.ts, record);
  }
});

app.action(rejectionReasonActionId, async ({ ack, body, action, client }) => {
  await ack();

  const blockId = action.block_id || "";
  const pageId = blockId.startsWith("rejection_reason:") ? blockId.slice("rejection_reason:".length) : "";
  const reason = action.selected_option?.value || "";
  if (!pageId || !reason) {
    console.error(`Missing rejection reason payload: page_id=${pageId} reason=${reason}`);
    return;
  }

  const reviewedBy = body.user?.id ? `slack:${body.user.id}` : "";
  const result = runFounderScan([
    "--set-rejection-reason",
    pageId,
    reason,
    "--reviewed-by",
    reviewedBy,
  ]);

  const channel = body.channel?.id;
  const messageTs = body.message?.ts;
  if (result.status !== 0) {
    console.error(result.stderr || result.stdout);
    if (channel && messageTs) {
      await addReaction(client, channel, messageTs, "warning");
    }
    return;
  }

  console.log((result.stdout || "").trim());
  if (channel && messageTs) {
    await client.chat.update({
      channel,
      ts: messageTs,
      text: `Rejection reason recorded: ${reason}`,
      blocks: [
        {
          type: "section",
          text: {
            type: "mrkdwn",
            text: `Rejection reason recorded: *${escapeMrkdwn(reason)}*`,
          },
        },
      ],
    });
  }
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
  if (watchlistEmoji.includes(reaction)) {
    return watchlistStatus;
  }
  return "";
}

function loadSent() {
  if (!fs.existsSync(sentPath)) {
    return { pages: {}, messages: {} };
  }
  return JSON.parse(fs.readFileSync(sentPath, "utf8"));
}

function runFounderScan(args) {
  return spawnSync("python3", ["founder_scan.py", ...args], {
    cwd: root,
    env: process.env,
    encoding: "utf8",
  });
}

async function postRejectionReasonPicker(client, channel, threadTs, record) {
  const name = record.name || "this candidate";
  await client.chat.postMessage({
    channel,
    thread_ts: threadTs,
    text: `Why reject ${name}?`,
    blocks: [
      {
        type: "section",
        block_id: `rejection_reason:${record.page_id}`,
        text: {
          type: "mrkdwn",
          text: `Why reject *${escapeMrkdwn(name)}*?`,
        },
        accessory: {
          type: "static_select",
          action_id: rejectionReasonActionId,
          placeholder: {
            type: "plain_text",
            text: "Select rejection reason",
          },
          options: rejectionReasons.map((reason) => ({
            text: {
              type: "plain_text",
              text: reason,
            },
            value: reason,
          })),
        },
      },
    ],
    unfurl_links: false,
    unfurl_media: false,
  });
}

function escapeMrkdwn(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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
    if (envAliases[key] && !process.env[envAliases[key]]) {
      process.env[envAliases[key]] = value;
    } else if (value.startsWith("xapp-") && !process.env.SLACK_APP_TOKEN) {
      process.env.SLACK_APP_TOKEN = value;
    } else if (value.startsWith("xoxb-") && !process.env.SLACK_BOT_TOKEN) {
      process.env.SLACK_BOT_TOKEN = value;
    } else if (/channel/i.test(key) && /^[CG][A-Z0-9]+$/.test(value) && !process.env.SLACK_CHANNEL_ID) {
      process.env.SLACK_CHANNEL_ID = value;
    }
  }
}
