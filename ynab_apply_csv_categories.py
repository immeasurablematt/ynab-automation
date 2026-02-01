#!/usr/bin/env python3
"""
Match YNAB transactions in [MBNA] Amazon.ca Rewards to amazon_ynab_ready.csv
and update Uncategorized transactions with categories from the CSV.
For unmatched Uncategorized, uses AI to identify category from memo.
"""
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

try:
    from dotenv import load_dotenv
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_script_dir, ".env"))
except ImportError:
    pass

import anthropic
import ynab
from ynab.rest import ApiException
from ynab.models.existing_transaction import ExistingTransaction
from ynab.models.put_transaction_wrapper import PutTransactionWrapper

ACCESS_TOKEN = os.environ.get("YNAB_ACCESS_TOKEN")
BUDGET_ID = os.environ.get("YNAB_BUDGET_ID")
ACCOUNT_ID = os.environ.get("YNAB_ACCOUNT_ID")
CSV_FILE = os.environ.get("YNAB_CSV_FILE", "amazon_ynab_ready.csv")


def load_csv_lookup(csv_path: str) -> tuple[dict[tuple[str, int], str], dict[int, list[tuple[str, str]]]]:
    """Build (date_str, amount_milliunits) -> category, and amount_milliunits -> [(date_str, cat)] for fuzzy match."""
    lookup = {}
    by_amount: dict[int, list[tuple[str, str]]] = {}
    with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = (row.get("Date") or "").strip()
            if not date_str:
                continue
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").date()
                date_str = dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
            amt_str = (row.get("Amount") or "0").strip().replace(",", "")
            try:
                amt = float(amt_str)
            except ValueError:
                continue
            # YNAB: outflow = negative milliunits
            milliunits = int(round(amt * 1000))
            if milliunits > 0:
                milliunits = -milliunits
            cat = (row.get("Category") or "").strip()
            if not cat or cat.lower() == "uncategorized":
                continue
            key = (date_str, milliunits)
            lookup[key] = cat
            if milliunits not in by_amount:
                by_amount[milliunits] = []
            by_amount[milliunits].append((date_str, cat))
    return lookup, by_amount


def _resolve_category(ai_response: str, valid: list[str]) -> str:
    """Fuzzy-match AI response to valid category."""
    ai_response = (ai_response or "").strip()
    if ai_response in valid:
        return ai_response
    import unicodedata
    def norm(s): return "".join(c for c in s if unicodedata.category(c) != "So").strip().lower()
    ai_n = norm(ai_response)
    for c in valid:
        if norm(c) == ai_n or ai_n in norm(c) or norm(c) in ai_n:
            return c
    return "Uncategorized"


def categorize_with_ai(memos: list[str], categories: list[str]) -> dict[int, str]:
    """Use Claude to categorize by memo. Returns {index: category}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {}
    client = anthropic.Anthropic(api_key=api_key)
    cat_list = "\n".join(f"- {c}" for c in categories)
    items = "\n".join(f"{i}. {m[:250]}" for i, m in enumerate(memos))
    prompt = f"""For each memo below (Amazon purchase), pick the best category. Use EXACT names from the list.

CATEGORIES:
{cat_list}

Memos:
{items}

Return ONLY JSON: {{"0": "CategoryName", "1": "..."}}. Use exact category names including emojis."""

    try:
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2048, messages=[{"role": "user", "content": prompt}])
        text = (r.content[0].text or "").strip()
        if not text:
            return {}
        # Extract JSON - handle markdown or leading text
        if text.startswith("```"):
            text = re.sub(r"```json?\s*", "", text).replace("```", "").strip()
        start = text.find("{")
        if start >= 0:
            end = text.rfind("}") + 1
            if end > start:
                text = text[start:end]
        out = json.loads(text)
        return {int(k): _resolve_category(str(v).strip(), categories) for k, v in out.items()}
    except Exception as e:
        print(f"  AI categorization failed: {e}")
        return {}


def main():
    if not ACCESS_TOKEN or not BUDGET_ID or not ACCOUNT_ID:
        print("Error: Set YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, YNAB_ACCOUNT_ID in .env")
        sys.exit(1)

    if not os.path.isfile(CSV_FILE):
        print(f"Error: CSV not found: {CSV_FILE}")
        sys.exit(1)

    print(f"Loading categories from {CSV_FILE}...")
    csv_lookup, csv_by_amount = load_csv_lookup(CSV_FILE)
    print(f"  Loaded {len(csv_lookup)} (date, amount) -> category mappings")

    configuration = ynab.Configuration(access_token=ACCESS_TOKEN)
    with ynab.ApiClient(configuration) as api_client:
        categories_api = ynab.CategoriesApi(api_client)
        transactions_api = ynab.TransactionsApi(api_client)

        # Get category name -> id
        try:
            cat_response = categories_api.get_categories(BUDGET_ID)
            category_name_to_id = {}
            for group in cat_response.data.category_groups or []:
                if getattr(group, "deleted", False) or getattr(group, "hidden", False):
                    continue
                for cat in group.categories or []:
                    if getattr(cat, "deleted", False) or getattr(cat, "hidden", False):
                        continue
                    category_name_to_id[cat.name] = cat.id
        except ApiException as e:
            print(f"Error fetching categories: {e}")
            sys.exit(1)

        # Fetch all transactions from account
        all_txs = []
        since = date(2025, 12, 1)
        while True:
            try:
                resp = transactions_api.get_transactions_by_account(
                    BUDGET_ID, ACCOUNT_ID, since_date=since
                )
                txs = resp.data.transactions or []
                for tx in txs:
                    if getattr(tx, "deleted", False):
                        continue
                    all_txs.append(tx)
                if len(txs) < 500 or not txs:
                    break
                latest = None
                for t in txs:
                    d = getattr(t, "var_date", None) or getattr(t, "date", None)
                    if d is None:
                        continue
                    if isinstance(d, str):
                        try:
                            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
                        except ValueError:
                            continue
                    if latest is None or d > latest:
                        latest = d
                if latest is None:
                    break
                since = latest + timedelta(days=1)
            except ApiException as e:
                print(f"Error fetching transactions: {e}")
                sys.exit(1)

        print(f"Found {len(all_txs)} transactions in YNAB account.")

        uncategorized = [t for t in all_txs if (getattr(t, "category_name", None) or "").strip() in ("", "Uncategorized")]
        print(f"  {len(uncategorized)} are Uncategorized")

        updated = 0
        no_match = []
        for tx in uncategorized:
            tx_date = getattr(tx, "var_date", None) or getattr(tx, "date", None)
            if tx_date is None:
                continue
            if isinstance(tx_date, str):
                date_str = tx_date[:10]
            else:
                date_str = tx_date.strftime("%Y-%m-%d")
            amt = getattr(tx, "amount", None)
            if amt is None:
                continue

            key = (date_str, amt)
            new_cat = csv_lookup.get(key)
            if not new_cat:
                # Try date ±2 days (bank vs order date)
                from datetime import datetime as dt, timedelta
                try:
                    d = dt.strptime(date_str, "%Y-%m-%d").date()
                    for delta in [-2, -1, 1, 2]:
                        adj = (d + timedelta(days=delta)).strftime("%Y-%m-%d")
                        if (adj, amt) in csv_lookup:
                            new_cat = csv_lookup[(adj, amt)]
                            break
                except ValueError:
                    pass
            if not new_cat and amt in csv_by_amount:
                # Fallback: same amount, use first CSV row (unique amounts)
                if len(csv_by_amount[amt]) == 1:
                    new_cat = csv_by_amount[amt][0][1]
            if not new_cat:
                no_match.append((date_str, amt, (getattr(tx, "memo") or "")[:80]))
                continue
            if new_cat not in category_name_to_id:
                print(f"  Warning: Category '{new_cat}' not in budget, skipping")
                continue

            existing = ExistingTransaction(
                account_id=tx.account_id,
                var_date=tx_date,
                amount=tx.amount,
                payee_id=getattr(tx, "payee_id", None),
                payee_name=getattr(tx, "payee_name", None),
                category_id=category_name_to_id[new_cat],
                memo=getattr(tx, "memo", None),
                cleared=getattr(tx, "cleared", None),
                approved=getattr(tx, "approved", None),
                flag_color=getattr(tx, "flag_color", None),
                subtransactions=None,
            )
            try:
                transactions_api.update_transaction(BUDGET_ID, tx.id, PutTransactionWrapper(transaction=existing))
                updated += 1
                print(f"  Updated: {date_str} ${amt/1000:.2f} -> {new_cat}")
            except ApiException as e:
                print(f"  Failed to update {tx.id}: {e}")

        # Second pass: AI categorize only when memos are detailed (YNAB often has just "Amazon" - useless)
        no_match_count = len(no_match)
        detailed_memos = [(d, a, m) for d, a, m in no_match if len((m or "").strip()) > 30]
        if detailed_memos and os.environ.get("ANTHROPIC_API_KEY"):
            no_match = detailed_memos
            print(f"\nRunning AI to categorize {len(no_match)} with detailed memos...")
            memos = [m for (_, _, m) in no_match]
            cat_names = [c for c in category_name_to_id.keys() if c != "Uncategorized"]
            # Process in batches of 25
            all_ai_cats = {}
            for b in range(0, len(memos), 25):
                batch_memos = memos[b:b+25]
                batch_cats = categorize_with_ai(batch_memos, cat_names)
                for k, v in batch_cats.items():
                    all_ai_cats[b + k] = v
            ai_cats = all_ai_cats
            if ai_cats:
                print(f"  AI returned {len(ai_cats)} categorizations")
            # Build map: (date, amount) -> tx for no-match items (from uncategorized)
            tx_by_key = {}
            for tx in uncategorized:
                td = getattr(tx, "var_date", None) or getattr(tx, "date", None)
                ds = td.strftime("%Y-%m-%d") if hasattr(td, "strftime") else (td[:10] if td else "")
                amt = getattr(tx, "amount", None)
                if ds and amt is not None:
                    tx_by_key[(ds, amt)] = tx
            ai_updated = 0
            for i, (d, amt_milli, memo) in enumerate(no_match):
                if i not in ai_cats or ai_cats[i] == "Uncategorized":
                    continue
                cat = ai_cats[i]
                if cat not in category_name_to_id:
                    continue
                key = (d, amt_milli)
                tx = tx_by_key.get(key)
                if not tx:
                    continue
                ex = ExistingTransaction(
                    account_id=tx.account_id,
                    var_date=getattr(tx, "var_date") or getattr(tx, "date"),
                    amount=tx.amount,
                    payee_id=getattr(tx, "payee_id", None),
                    payee_name=getattr(tx, "payee_name", None),
                    category_id=category_name_to_id[cat],
                    memo=getattr(tx, "memo", None),
                    cleared=getattr(tx, "cleared", None),
                    approved=getattr(tx, "approved", None),
                    flag_color=getattr(tx, "flag_color", None),
                    subtransactions=None,
                )
                try:
                    transactions_api.update_transaction(BUDGET_ID, tx.id, PutTransactionWrapper(transaction=ex))
                    ai_updated += 1
                    print(f"  AI updated: {d} ${amt_milli/1000:.2f} -> {cat}")
                except ApiException as e:
                    print(f"  Failed {tx.id}: {e}")
            updated += ai_updated
            print(f"  AI categorized {ai_updated} more.")

        print(f"\nTotal: Updated {updated} Uncategorized transaction(s).")
        if no_match_count > 0:
            print(f"\n{no_match_count} Uncategorized had no CSV match.")


if __name__ == "__main__":
    main()
