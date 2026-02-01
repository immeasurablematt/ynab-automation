#!/usr/bin/env python3
"""
Import transactions from a CSV into YNAB with category assignment.
Uses .env for YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, YNAB_ACCOUNT_ID.
Skips duplicates: same amount, date within +/- DAYS_TOLERANCE of an existing transaction.
"""
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import ynab
from ynab.rest import ApiException

# --- Configuration (env vars; override CSV path here if needed) ---
ACCESS_TOKEN = os.environ.get("YNAB_ACCESS_TOKEN")
BUDGET_ID = os.environ.get("YNAB_BUDGET_ID")
ACCOUNT_ID = os.environ.get("YNAB_ACCOUNT_ID")
CSV_FILE = os.environ.get("YNAB_CSV_FILE", "transactions.csv")

# Payee (lowercase) -> Category name (must match a category in your budget)
CATEGORY_MAPPING = {
    "amazon.ca": "Online Shopping",
    "sobeys": "Groceries",
    "tim hortons": "Dining Out",
    "payroll - employer name": "Income",
}
DEFAULT_CATEGORY_NAME = "Uncategorized"

# Duplicate check: skip if existing tx has same amount and date within +/- N days
DAYS_TOLERANCE = int(os.environ.get("YNAB_DUPLICATE_DAYS", "5"))


def main():
    if not ACCESS_TOKEN or not BUDGET_ID or not ACCOUNT_ID:
        print("Error: Set YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, and YNAB_ACCOUNT_ID in .env")
        print("  Run: python get_ynab_ids.py to see your budget and account IDs.")
        sys.exit(1)

    if not os.path.isfile(CSV_FILE):
        print(f"Error: CSV file not found: {CSV_FILE}")
        sys.exit(1)

    configuration = ynab.Configuration(access_token=ACCESS_TOKEN)
    with ynab.ApiClient(configuration) as api_client:
        categories_api = ynab.CategoriesApi(api_client)
        transactions_api = ynab.TransactionsApi(api_client)

        # Fetch categories: name -> id
        try:
            categories_response = categories_api.get_categories(BUDGET_ID)
            category_groups = categories_response.data.category_groups
            category_id_map = {}
            for group in category_groups:
                for cat in group.categories:
                    if not getattr(cat, "deleted", True) and not getattr(cat, "hidden", False):
                        category_id_map[cat.name.lower()] = cat.id
            print("Categories fetched successfully.")
        except ApiException as e:
            print(f"Error fetching categories: {e}")
            sys.exit(1)

        # First pass: read CSV rows; support optional OrderId for grouping (splits)
        raw_rows = []
        min_csv_date = None
        max_csv_date = None
        with open(CSV_FILE, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = row.get("Date", "").strip()
                if not date_str:
                    continue
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    try:
                        date_obj = datetime.strptime(date_str, "%m/%d/%Y").date()
                    except ValueError:
                        continue
                amount_str = row.get("Amount", "0").strip().replace(",", "")
                try:
                    raw = float(amount_str)
                    amount = int(round(raw * 1000))
                    if amount > 0:
                        amount = -amount
                except ValueError:
                    continue
                if amount == 0:
                    continue
                order_id = (row.get("OrderId") or "").strip()
                raw_rows.append((date_obj, amount, row, order_id))
                if min_csv_date is None or date_obj < min_csv_date:
                    min_csv_date = date_obj
                if max_csv_date is None or date_obj > max_csv_date:
                    max_csv_date = date_obj

        # Group by OrderId (when present); else each row is its own group
        groups = defaultdict(list)
        for date_obj, amount, row, order_id in raw_rows:
            if order_id:
                groups[order_id].append((date_obj, amount, row))
            else:
                groups[f"{date_obj}|{amount}|{(row.get('Memo') or '')[:60]}"].append((date_obj, amount, row))

        # Fetch existing transactions for duplicate check (paginate if API returns many)
        existing_by_amount = {}  # amount -> [date, ...]
        PAGE_SIZE = 500
        if min_csv_date is not None:
            since = (min_csv_date - timedelta(days=DAYS_TOLERANCE)).isoformat()
            try:
                while True:
                    existing_response = transactions_api.get_transactions_by_account(
                        BUDGET_ID, ACCOUNT_ID, since_date=since
                    )
                    txs = existing_response.data.transactions or []
                    for tx in txs:
                        if getattr(tx, "deleted", False):
                            continue
                        amt = tx.amount
                        tx_date = getattr(tx, "var_date", None) or getattr(tx, "date", None)
                        if tx_date is None:
                            continue
                        dt_str = tx_date.isoformat() if hasattr(tx_date, "isoformat") else str(tx_date)
                        try:
                            dt_obj = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            continue
                        if amt not in existing_by_amount:
                            existing_by_amount[amt] = []
                        existing_by_amount[amt].append(dt_obj)
                    if len(txs) < PAGE_SIZE or not txs:
                        break
                    latest = None
                    for tx in txs:
                        tx_date = getattr(tx, "var_date", None) or getattr(tx, "date", None)
                        if tx_date is None:
                            continue
                        dt_str = tx_date.isoformat() if hasattr(tx_date, "isoformat") else str(tx_date)
                        try:
                            dt_obj = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            continue
                        if latest is None or dt_obj > latest:
                            latest = dt_obj
                    if latest is None:
                        break
                    since = (latest + timedelta(days=1)).isoformat()
                print(f"Loaded {sum(len(v) for v in existing_by_amount.values())} existing transaction(s) for duplicate check.")
            except ApiException as e:
                print(f"Warning: Could not fetch existing transactions for duplicate check: {e}")

        def is_duplicate(import_date, import_amount):
            if import_amount not in existing_by_amount:
                return False
            for existing_date in existing_by_amount[import_amount]:
                if abs((import_date - existing_date).days) <= DAYS_TOLERANCE:
                    return True
            return False

        def category_for_row(row):
            row_category = (row.get("Category") or "").strip()
            if row_category:
                return category_id_map.get(row_category.lower()), row_category
            payee_lower = (row.get("Payee") or "").strip().lower()
            name = CATEGORY_MAPPING.get(payee_lower, DEFAULT_CATEGORY_NAME)
            return category_id_map.get(name.lower()) if name else None, name

        # Build list of NewTransaction (single or split) per group, skipping duplicates
        transactions_to_import = []
        skipped_duplicates = 0
        for _key, group_rows in groups.items():
            date_obj = group_rows[0][0]
            total_amount = sum(r[1] for r in group_rows)
            if total_amount == 0:
                continue
            if is_duplicate(date_obj, total_amount):
                skipped_duplicates += 1
                continue

            payee = (group_rows[0][2].get("Payee") or "").strip() or "Amazon.ca"
            memo = (group_rows[0][2].get("Memo") or "").strip()[:500]

            # Aggregate by category (category_id -> sum of amounts in milliunits)
            cat_amounts = defaultdict(int)
            for _d, amt, row in group_rows:
                cat_id, cat_name = category_for_row(row)
                if cat_name and not cat_id:
                    print(f"Warning: Category '{cat_name}' not found. Leaving uncategorized.")
                cat_amounts[cat_id] += amt

            if len(cat_amounts) == 1:
                # Single category (including uncategorized): one transaction
                only_cat_id = next(iter(cat_amounts.keys()))
                import_id = f"YNAB:{total_amount}:{date_obj.isoformat()}:1"
                tx = ynab.NewTransaction(
                    account_id=ACCOUNT_ID,
                    date=date_obj,
                    amount=total_amount,
                    payee_name=payee or None,
                    memo=memo or None,
                    category_id=only_cat_id,
                    cleared="uncleared",
                    approved=False,
                    import_id=import_id,
                )
                transactions_to_import.append(tx)
            elif len(cat_amounts) > 1:
                # Multiple categories: split transaction
                subtransactions = []
                for cid, camt in cat_amounts.items():
                    subtransactions.append(ynab.SaveSubTransaction(amount=camt, category_id=cid, memo=None))
                import_id = f"YNAB:{total_amount}:{date_obj.isoformat()}:1"
                split_memo = memo or f"Order {date_obj.isoformat()} (split)"
                tx = ynab.NewTransaction(
                    account_id=ACCOUNT_ID,
                    date=date_obj,
                    amount=total_amount,
                    payee_name=payee or None,
                    memo=split_memo[:500],
                    category_id=None,
                    cleared="uncleared",
                    approved=False,
                    import_id=import_id,
                    subtransactions=subtransactions,
                )
                transactions_to_import.append(tx)

        if skipped_duplicates:
            print(f"Skipped {skipped_duplicates} duplicate(s) (same amount, date within ±{DAYS_TOLERANCE} days).")

        if not transactions_to_import:
            print("No transactions to import.")
            return

        try:
            wrapper = ynab.PostTransactionsWrapper(transactions=transactions_to_import)
            response = transactions_api.create_transaction(BUDGET_ID, wrapper)
            created = response.data.transactions or []
            duplicate = getattr(response.data, "duplicate_import_ids", None) or []
            print(f"Imported {len(created)} transaction(s).")
            if duplicate:
                print(f"Skipped {len(duplicate)} duplicate(s).")
        except ApiException as e:
            print(f"Error importing transactions: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
