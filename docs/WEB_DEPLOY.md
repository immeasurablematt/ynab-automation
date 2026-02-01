# Deploy YNAB Import to Vercel

## 1. Push to GitHub

If not already in a repo:

```bash
cd "/Users/matthewbaggetta/Library/Mobile Documents/com~apple~CloudDocs/Coding Projects/YNAB Automation"
git init
git add .
git commit -m "YNAB import web app"
git branch -M main
# Create a new repo on GitHub, then:
git remote add origin https://github.com/YOUR_USERNAME/ynab-automation.git
git push -u origin main
```

## 2. Deploy to Vercel

1. Go to [vercel.com](https://vercel.com) and sign in (use GitHub).
2. Click **Add New** → **Project**.
3. Import your GitHub repo (`ynab-automation` or your repo name).
4. Before deploying, add **Environment Variables**:
   - `YNAB_ACCESS_TOKEN` – Your YNAB Personal Access Token
   - `YNAB_BUDGET_ID` – Your budget ID (e.g. from `get_ynab_ids.py`)
   - `YNAB_ACCOUNT_ID` – Your account ID (e.g. [MBNA] Amazon.ca Rewards)
   - Optional: `YNAB_DUPLICATE_DAYS` – Days tolerance for duplicate check (default 5)
5. Click **Deploy**.

## 3. Use the app

1. Open your Vercel URL (e.g. `https://ynab-automation.vercel.app`).
2. **Convert Amazon export**: Upload your CSV from the Amazon Order History Reporter extension, click Convert & Download, then review the output.
3. **Import to YNAB**: Upload the YNAB-ready CSV (from Convert or your own), click Import to YNAB.
4. Check results (imported count, duplicates skipped).

## Security

- Credentials live only in Vercel env vars.
- The app runs server-side; your token is never exposed to the browser.
- Deploy from a private GitHub repo if you prefer.
- Regenerate your YNAB token if it’s ever exposed.
