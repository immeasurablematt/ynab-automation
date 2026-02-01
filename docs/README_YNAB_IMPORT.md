# YNAB transaction import (with categories)

**Fastest path (Amazon orders):** See [OPTION_A_INSTRUCTIONS.md](OPTION_A_INSTRUCTIONS.md) for the Chrome extension + normalizer workflow.

Import CSV transactions into You Need A Budget with **categories assigned** via the official YNAB API.

## What you need to begin

1. **Personal Access Token**  
   You have one. Store it in `.env` (see below) and consider regenerating it in YNAB after sharing it anywhere, since it was exposed in chat.

2. **Budget ID and Account ID**  
   Run the helper script once to list them:
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   # Edit .env and set YNAB_ACCESS_TOKEN=your_token
   python get_ynab_ids.py
   ```
   Copy the **Budget ID** and the **Account ID** (for the account you want to import into) into `.env`.

3. **`.env` file**  
   Copy `.env.example` to `.env` and set:
   - `YNAB_ACCESS_TOKEN` – your YNAB Personal Access Token  
   - `YNAB_BUDGET_ID` – from step 2  
   - `YNAB_ACCOUNT_ID` – from step 2  
   Optionally: `YNAB_CSV_FILE` – path to your CSV (default: `transactions.csv`).

4. **CSV file**  
   Use columns: **Date**, **Payee**, **Memo**, **Amount**.  
   - Date: `YYYY-MM-DD` or `MM/DD/YYYY`  
   - Amount: decimal (negative = outflow, positive = inflow)  
   Example: `transactions_template.csv`

5. **Category mapping**  
   In `ynab_import.py`, edit `CATEGORY_MAPPING`: keys = payee (lowercase), values = **exact category name** from YNAB (from `get_ynab_ids.py` output). Set `DEFAULT_CATEGORY_NAME` for unmapped payees (e.g. `"Uncategorized"`).

## Commands

```bash
# Install
pip install -r requirements.txt

# 1) Get your Budget ID, Account ID, and category names
python get_ynab_ids.py

# 2) Fill .env (YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, YNAB_ACCOUNT_ID)
# 3) Put your transactions in transactions.csv (or set YNAB_CSV_FILE)
# 4) Edit CATEGORY_MAPPING in ynab_import.py

# Import
YNAB_CSV_FILE=amazon_ynab_ready.csv python ynab_import.py
```

**Other scripts:**
- `ynab_apply_csv_categories.py` — Match existing YNAB transactions to your CSV and fix Uncategorized
- `ynab_cleanup_amazon.py` — Remove duplicates and verify categories in the Amazon account

## Security

- **Do not commit `.env`** (it’s in `.gitignore`).  
- Prefer regenerating your YNAB Personal Access Token if it was ever shared.
