# Deploying the public live demo

About 25 minutes, all on free plans, no credit card. When you're done, anyone with your link can:

- watch a real server's live CPU, memory and disk;
- run built-in read-only commands on it, with no install;
- connect their own computer with one copy-paste command, into a private sandbox that's
  deleted after 60 minutes.

## How the pieces fit

| Piece | Hosted on | Why there |
|---|---|---|
| Dashboard (`web/`) | **Vercel** | Static page, fast, a clean `vercel.app` link to share |
| API + the live demo server (`app/`, `agent/`) | **Render** (free web service) | Needs an always-running process: agents hold WebSockets open, and background jobs mark devices offline and delete expired sandboxes |
| Database | **Neon** (free Postgres) | Permanent. Render's own free Postgres is deleted after 30 days |

Vercel's functions can't run this API: WebSocket connections end when a function hits its time
limit, memory isn't shared between instances, and nothing runs in the background.

**What the free plans mean for visitors:** Render puts the API to sleep after 15 minutes without
visitors. The first visitor after that sees a "Starting the demo server" screen for about a
minute. Open the link yourself a minute before you send it to someone or join an interview.
Don't add an uptime pinger to keep it awake all month: that would also keep the database
awake and use up Neon's free compute hours.

---

## Step 0: Put the code on `main`

Render and Vercel deploy from a branch. Merge `v2-working` into `main` (GitHub → **Pull requests**
→ **New** → base `main`, compare `v2-working` → **Create** → **Merge**), or locally:

```bash
git checkout main && git pull && git merge v2-working && git push
```

## Step 1: Database on Neon (5 minutes)

1. Go to **https://neon.tech** → **Sign up** → continue with GitHub.
2. Create a project: name `fleet-manager`, region **AWS US East 2 (Ohio)**. That's next to the
   Render region used below, so every database call is short.
3. On the project dashboard click **Connect**. Copy the connection string. It looks like
   `postgresql://neondb_owner:…@ep-something-123456.us-east-2.aws.neon.tech/neondb?sslmode=require…`
   Either the pooled or the direct string works; the app adapts to both.
4. Keep it somewhere private for the next step. It contains the database password.

## Step 2: API on Render (10 minutes)

1. Go to **https://render.com** → **Get started** → sign up with GitHub. Allow access to the
   `linux-fleet-manager` repository.
2. Click **New** → **Blueprint** → choose `linux-fleet-manager`, branch `main`. Render reads
   `render.yaml` and shows a service called **fleet-manager-api**.
3. It asks for four values:

   | Key | What to enter |
   |---|---|
   | `DATABASE_URL` | the Neon connection string from step 1 |
   | `ADMIN_USERNAME` | your login for the dashboard, e.g. `sachit` |
   | `ADMIN_PASSWORD` | a strong password, 8–72 characters, not reused anywhere |
   | `CORS_ORIGINS` | for now `https://example.com`; you'll replace it in step 4 |

   `SECRET_KEY` and `AGENT_TOKEN` are generated for you. Nobody needs to see them.
4. Click **Apply**. The first build takes 3–5 minutes. Watch **Logs** until you see
   `Application startup complete` and `connected to http://127.0.0.1:… as live-demo-server`.
5. Copy the service URL from the top of the page, e.g. `https://fleet-manager-api.onrender.com`.
   Open `<that URL>/health`; you should see `"demo":true`.
   Opening the URL itself already shows the full dashboard, served by the API.

## Step 3: Dashboard on Vercel (5 minutes)

1. Point the dashboard at your API. In GitHub, open `web/config.js`, click the pencil icon,
   change the last line to your Render URL (no slash at the end), and commit to `main`:
   ```js
   window.FLEET_API = "https://fleet-manager-api.onrender.com";
   ```
2. Go to **https://vercel.com** → sign up with GitHub → **Add New…** → **Project** → import
   `linux-fleet-manager`.
3. On the configure screen:
   - **Root Directory**: click **Edit** → choose `web`
   - **Framework Preset**: Other (no build command, no output directory)
   - Click **Deploy**.
4. Copy your site's address, e.g. `https://linux-fleet-manager.vercel.app`. For a nicer name,
   go to **Settings → Domains**, edit the `.vercel.app` domain (e.g. `sachit-fleet.vercel.app`),
   and use that address below.

## Step 4: Let the dashboard talk to the API

Back in Render → **fleet-manager-api** → **Environment** → edit `CORS_ORIGINS` → set it to
your exact Vercel address: `https`, no slash at the end. For several, separate them with commas.
**Save, rebuild, and deploy.**

## Step 5: Test it like a recruiter

Open your Vercel link in a private window:

1. Wait for the waking screen to finish, if it shows.
2. The hero card shows **live-demo-server** with a moving CPU line.
3. Click **Run a command on it** → **Top processes**. The three-step strip fills in and the output appears.
4. Click **Add your computer** → **Get my command** → paste it into Terminal on your Mac.
   Within about 30 seconds the page says your Mac is connected and selects it. Run **System info**.
5. Press Ctrl+C in Terminal. The Mac goes offline on the dashboard, and the temporary folder is gone.
6. Open the link on your phone: the live server and its commands work there too.

**Owner sign in** (top right) with `ADMIN_USERNAME` / `ADMIN_PASSWORD` shows every sandbox.
Shell commands are still blocked on visitors' machines, even for you.

## Updating

Push to `main`. Render and Vercel both redeploy automatically. A Render redeploy restarts the
live demo server; it keeps its identity and history because `SHOWCASE_DEVICE_ID` pins it.

## If something's wrong

| Symptom | Fix |
|---|---|
| Stuck on "Starting the demo server" for more than 2 minutes | Open `<Render URL>/health` directly. If that works, `web/config.js` has the wrong URL. If not, check Render → **Logs**. |
| Page loads, then "Request failed" / CORS errors in the browser console | `CORS_ORIGINS` must be exactly your Vercel origin: `https://…vercel.app`, no slash. Redeploy after changing it. |
| No **live-demo-server** in the list | Render logs: the agent line should say `connected`. `AGENT_TOKEN` must exist (Blueprint generates it). |
| Render logs show database errors | Re-copy `DATABASE_URL` from Neon; check the Neon project isn't suspended. |
| Installer says Python is missing | macOS: `xcode-select --install`. Windows: python.org (tick "Add to PATH"). Ubuntu: `sudo apt install python3 python3-venv`. |
| "The public demo is full" | Too many visitor devices at once (limit 100). Old ones are deleted when their hour is up. |

## Limits worth knowing (all configurable as Render environment variables)

| Setting | Default | Meaning |
|---|---|---|
| `DEMO_SESSION_MINUTES` | 60 | how long a visitor's sandbox lives |
| `DEMO_MAX_DEVICES` | 3 | devices per sandbox |
| `DEMO_MAX_TOTAL_DEVICES` | 100 | across all sandboxes |
| `DEMO_SESSIONS_PER_IP_PER_HOUR` | 6 | new sandboxes per visitor IP |
| `DEMO_COMMANDS_PER_IP_PER_MINUTE` | 20 | commands per visitor IP |
| `RETENTION_HOURS` | 24 | older metrics, commands and resolved alerts are deleted |

Free-plan quotas at the time of writing: Render gives 750 instance hours a month, and the
service sleeps after 15 idle minutes. Neon gives 1 GB of storage and 100 compute-hours a month,
scales to zero after 5 idle minutes, and never expires.
