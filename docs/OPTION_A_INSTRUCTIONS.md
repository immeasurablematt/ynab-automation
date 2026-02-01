# Option A: Amazon orders via Chrome extension (fastest)

Use the **Amazon Order History Reporter** Chrome extension to export your orders to CSV, then run the normalizer script to get a YNAB-ready file. No PDFs, no manual typing.

## 1. Install the extension

1. Open Chrome and go to the [Amazon Order History Reporter](https://chromewebstore.google.com/detail/amazon-order-history-repo/mgkilgclilajckgnedgjgnfdokkgnibi) page.
2. Click **Add to Chrome** and confirm.
3. (Optional) Pin the extension: click the puzzle piece icon (top right), then pin **Amazon Order History Reporter** so the orange **A** icon is visible.

## 2. Export your Amazon orders

1. Go to **Amazon.ca** and sign in.
2. Open **Account & Lists** → **Your Account** → **Your Orders** (or go to [amazon.ca/gp/your-account/order-history](https://www.amazon.ca/gp/your-account/order-history)).
3. Click the **orange A** extension icon in the Chrome toolbar.
4. In the popup, click a **year button** (e.g. 2025) to scrape that year’s orders. Wait for the table to finish loading (may take 30–60 seconds for many orders).
5. Use the **blue download button** to save the CSV.
   - **Orders CSV**: one row per order, with order total.
   - **Items CSV** (often more useful): one row per item, with item-level details.
6. Save the file somewhere (e.g. Downloads or your project folder). Note the filename (e.g. `amazon_orders_2025.csv`).

## 3. Convert to YNAB format

From the project folder, run:

```bash
cd "/Users/matthewbaggetta/Library/Mobile Documents/com~apple~CloudDocs/Coding Projects/YNAB Automation"
python3 amazon_csv_to_ynab.py /path/to/your/amazon_export.csv
```

Example:

```bash
python3 amazon_csv_to_ynab.py ~/Downloads/amazon_orders_2025.csv
```

Optional flags:

- `-o output.csv` – custom output filename (default: `amazon_ynab_ready.csv`)
- `--no-ai` – skip AI categorization; all rows will be Uncategorized

The script will:

- Detect date, amount, and memo columns (supports both Orders and Items CSV).
- Normalize dates to YYYY-MM-DD.
- Use negative amounts for spending and positive for refunds.
- **AI categorization:** Uses Claude to understand each product and pick the right budget category (requires `ANTHROPIC_API_KEY` in `.env`).

## 4. Review the output

Open `amazon_ynab_ready.csv` (or your custom output file) and check:

- Dates and amounts.
- **Category** – edit any that are wrong.
- Memos – shorten if needed (YNAB memo limit is 500 characters).

## 5. Import into YNAB

After reviewing:

```bash
YNAB_CSV_FILE=amazon_ynab_ready.csv python3 ynab_import.py
```

Transactions will be imported into your [MBNA] Amazon.ca Rewards account in Budget-ta 2.0.

---

## Troubleshooting

**"Could not find date and amount columns"**  
The extension’s CSV format may have changed. Share the first few lines (header + 1–2 rows) and the script can be updated.

**Too many or too few rows**  
- **Orders CSV**: one transaction per order.  
- **Items CSV**: one transaction per item (multiple rows per order). If you want one per order, use the Orders CSV.

**Category wrong or blank**  
Edit the CSV manually. AI uses your YNAB categories; ensure `YNAB_ACCESS_TOKEN` and `YNAB_BUDGET_ID` are set so it fetches your actual category list.

**Extension doesn’t work on Amazon.ca**  
The extension supports Amazon.ca. If it fails, try: clear Amazon cookies, sign in again, and run the export with only one Amazon tab open.
