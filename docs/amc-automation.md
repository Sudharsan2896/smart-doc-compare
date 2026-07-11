# AMC Monitor — daily automation

The **🔔 AMC Monitor** tool in the app is interactive. `run_amc.py` +
`.github/workflows/amc-monitor.yml` make it **autonomous**: every morning GitHub
Actions reads your AMC register, decides what's expired / critical / due, and
delivers a digest with ready-to-send reminder drafts.

## What the daily run does

1. Reads the register (`AMC_REGISTER_PATH`, default `samples/amc/amc_register.csv`).
2. Classifies every contract by expiry (deterministic date math — the AI never
   decides urgency).
3. Writes a digest to **three places**: the Action **run Summary**, an uploaded
   **artifact** (`amc_digest.md` + `amc_action_report.csv`), and — if SMTP is
   configured — an **email** to your procurement inbox.

**It does not email vendors.** Auto-sending outward mail from an unattended job is
a hard-to-reverse action that should have a human in the loop. The digest goes to
*you*; you review the drafts and send them.

Out of the box (no configuration) the workflow already runs daily and shows the
digest in the Actions run Summary — email and LLM drafting are optional add-ons.

## Enable email delivery

Add these as repository **Secrets** (Settings → Secrets and variables → Actions →
Secrets). Set all four of host/user/password/to to turn email on:

| Secret | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (default) |
| `SMTP_USER` | `procurement@yourorg.org` |
| `SMTP_PASSWORD` | an app password (not your login password) |
| `AMC_EMAIL_FROM` | `procurement@yourorg.org` (defaults to `SMTP_USER`) |
| `AMC_EMAIL_TO` | who receives the digest |

For Gmail, create an **App Password** (requires 2FA) rather than using your
account password.

## Point it at your real register

Set the repo **Variable** `AMC_REGISTER_PATH` to your register's path in the repo,
or leave it on the sample. Supported formats: `.csv` (no dependencies) and `.xlsx`.

> ⚠️ **Privacy:** an AMC register can contain vendor and pricing data. If your repo
> is public, do **not** commit real data — keep the repo private, or fetch the
> register from a secure location in a step you add before `run_amc.py`.

If your column headers differ from the sample, set these **Variables** (otherwise
they're auto-detected): `AMC_COL_ASSET`, `AMC_COL_VENDOR`, `AMC_COL_END`,
`AMC_COL_VALUE`, `AMC_COL_OWNER`, `AMC_COL_CONTACT`.

Tune the thresholds with Variables `AMC_CRITICAL_DAYS` (default 15) and
`AMC_DUE_DAYS` (default 45).

## Optional: let an LLM write the drafts

By default the reminder drafts use a deterministic template (zero dependencies).
To have Claude or Gemini write them instead, set Variable `AMC_USE_LLM=1` and add
the matching Secret (`ANTHROPIC_API_KEY` or `GEMINI_API_KEY`). The workflow then
installs the SDK automatically. The drafts are still built from the computed
facts, with an explicit instruction not to invent any dates or figures.

## Schedule

The cron is `30 3 * * *` (03:30 UTC ≈ 09:00 IST). Edit it in the workflow (cron is
always UTC). You can also trigger a run any time from the **Actions** tab
(**Run workflow**) — useful for testing your secrets/variables.

## Run it locally

```bash
AMC_REGISTER_PATH=samples/amc/amc_register.csv python run_amc.py
```

Prints the digest and writes `amc_digest.md` + `amc_action_report.csv`.
