# Deploying Recruiter Intelligence (FREE)

Run your recruiting intelligence system 24/7 for essentially **$0/month**.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  GitHub Actions │     │  Render (Free)  │     │    Supabase     │
│                 │     │                 │     │     (Free)      │
│  Runs every     │────▶│   Dashboard     │────▶│   PostgreSQL    │
│  6 hours        │     │   (on-demand)   │     │   Database      │
│  FREE           │     │   FREE          │     │   FREE          │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Costs

| Component | Service | Cost |
|-----------|---------|------|
| Worker (cron) | GitHub Actions | **FREE** |
| Dashboard | Render Free Tier | **FREE** |
| Database | Supabase Free | **FREE** |
| LLM API | Gemini | ~$5/month |
| **Total** | | **~$5/month** |

---

## Step 1: Push to GitHub

```bash
cd /Users/gauravsurtani/projects/fir_recruiting/new_approach/recruiter-intelligence

git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/recruiter-intelligence.git
git push -u origin main
```

---

## Step 2: Add GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `DATABASE_URL` | Your Supabase pooler URL (from Supabase Dashboard → Connect) |
| `GEMINI_API_KEY` | Your Gemini API key (from Google AI Studio) |

---

## Step 3: Enable GitHub Actions

1. Go to your repo → **Actions** tab
2. Click **"I understand my workflows, go ahead and enable them"**
3. You'll see the "Pipeline" workflow

The workflow runs automatically every 6 hours. To run manually:
- Go to **Actions** → **Pipeline** → **Run workflow**

---

## Step 4: Deploy Dashboard to Render (Optional)

If you want a web dashboard:

1. Go to https://dashboard.render.com
2. Click **New** → **Web Service**
3. Connect your GitHub repo
4. Settings:
   - **Name**: `recruiter-intel`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python scripts/kg_viewer.py`
   - **Plan**: **Free**
5. Add environment variables:
   - `DATABASE_URL` = your Supabase pooler URL
   - `RI_GEMINI_API_KEY` = your Gemini key
6. Deploy

**Note**: Free tier spins down after 15 min of inactivity. First visit takes ~30 seconds to wake up.

---

## How It Works

**GitHub Actions** (`.github/workflows/pipeline.yml`) runs every 6 hours:

1. **Fetch**: Gets articles from 30+ RSS feeds
2. **Classify**: Identifies funding, acquisitions, layoffs, exec moves
3. **Extract**: Uses Gemini to extract companies, people, relationships
4. **Store**: Saves everything to Supabase

**Dashboard** (optional): View the data anytime at your Render URL.

---

## Schedule

The pipeline runs at:
- 12:00 AM UTC (midnight)
- 6:00 AM UTC
- 12:00 PM UTC (noon)
- 6:00 PM UTC

You can also trigger it manually from GitHub Actions.

---

## Monitoring

### Check Pipeline Runs
Go to your repo → **Actions** → **Pipeline**

You'll see:
- Run history
- Logs for each run
- Success/failure status

### Check Data
Either:
- Visit your Render dashboard URL
- Query Supabase directly via their dashboard

---

## Troubleshooting

### "Connection refused" in GitHub Actions
- Check `DATABASE_URL` secret is set correctly
- Make sure you're using the **pooler** URL (port 6543)

### Pipeline not running
- Go to Actions tab, make sure workflows are enabled
- Check if the repo is public (free Actions) or you have Actions minutes remaining

### Dashboard slow to load
- Normal for free tier - takes ~30s to spin up after idle
- Upgrade to Starter ($7/mo) for always-on

---

## Upgrading Later

If you want faster dashboard response:
- Change `plan: free` to `plan: starter` in render.yaml ($7/month)

If you want more frequent updates:
- Edit the cron schedule in `.github/workflows/pipeline.yml`

---

## Local Development

You can still run everything locally:

```bash
# Dashboard
python scripts/kg_viewer.py

# Manual pipeline run
python scripts/run_daily.py
```

Both will use your Supabase database via the `.env` file.
