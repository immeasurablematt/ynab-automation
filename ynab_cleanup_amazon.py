#!/usr/bin/env python3
"""
Clean up [MBNA] Amazon.ca Rewards account:
1. Fetch transactions in date range
2. Delete duplicates (same amount + same date; keep first)
3. Use AI to verify categories; fix any that are incorrect.
Uses .env for YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, YNAB_ACCOUNT_ID, ANTHROPIC_API_KEY.
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

START_DATE = date(2025, 12, 2)
END_DATE = date(2026, 1, 31)


def get_ai_categories(items: list[dict], categories: list[str]) -> dict[str, str]:
    """
    Use Claude to determine the correct category for each transaction.
    items: list of {"id": tx_id, "memo": memo, "current_category": category_name}
    Returns: {tx_id: correct_category_name}
    """
    if not ANTHROPIC_API_KEY:
        print("Warning: ANTHROPIC_API_KEY not set; skipping AI categorization.")
        return {}
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    category_list = "\n".join(f"- {cat}" for cat in categories)
    
    items_text = ""
    for item in items:
        memo = (item.get("memo") or "")[:200]
        current = item.get("current_category", "")
        items_text += f"ID:{item['id']} | Current: {current} | Memo: {memo}\n"
    
    prompt = f"""You are a budget categorization assistant. For each Amazon transaction below, determine the CORRECT category based on what the product actually is.

AVAILABLE CATEGORIES:
{category_list}

TRANSACTIONS TO VERIFY:
{items_text}

INSTRUCTIONS:
1. Understand what each product actually is (reason about it, don't just keyword match)
2. Key category mappings:
   - Kids clothing, toys, educational items, diapers → "Kids Supplies"
   - Adult clothing, shoes, accessories → "Wardrobe"  
   - Movie/TV rentals (individual purchases) → "Family Fun & Dates"
   - Streaming subscriptions (Apple TV+, Prime Video ad-free, recurring apps) → "Subscriptions (Monthly)"
   - Health supplements, vitamins for adults → "Medicine & Vitamins"
   - Light fixtures, sconces, bulbs → "Light Fixtures" if available, else "Home Maintenance & Decor"
   - Furniture (coffee tables, ottomans) → "Coffee Table & Side Tables" if available, else "Home Maintenance & Decor"
   - Cleaning supplies, kitchenware, tools, home hardware → "Home Maintenance & Decor"
   - Gift cards → "Gifts & Giving"
   - Spiritual/Buddhist/meditation books → "Retreats"
   - Tech gadgets for personal use → "Matt's Fun Money 🤑"
   - Women's clothing, lingerie, women's books → "Wardrobe" or "Sheva's Fun Money 💸"
   - Adult fiction/personal reading → appropriate Fun Money category
   - Child safety items (locks, gates, monitors) → "Kids Supplies"
   - Bedding, blankets → "Bedroom Set" if available, else "Home Maintenance & Decor"
3. If the memo is too short/vague (like just "Amazon..."), keep the current category unchanged
4. Only suggest a change if you're confident the current category is WRONG

Return ONLY a JSON object mapping transaction ID to the CORRECT category.
Only include transactions that need to be CHANGED (don't include ones that are already correct).
Example: {{"abc123": "Kids Supplies", "def456": "Subscriptions (Monthly)"}}

If all categories are correct, return: {{}}

JSON response:"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = response.content[0].text.strip()
        if response_text.startswith("```"):
            response_text = re.sub(r"```json?\s*", "", response_text)
            response_text = re.sub(r"```\s*$", "", response_text)
        
        return json.loads(response_text)
    
    except Exception as e:
        print(f"Warning: AI categorization failed: {e}")
        return {}


def main():
    if not ACCESS_TOKEN or not BUDGET_ID or not ACCOUNT_ID:
        print("Error: Set YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, and YNAB_ACCOUNT_ID in .env")
        sys.exit(1)

    configuration = ynab.Configuration(access_token=ACCESS_TOKEN)
    with ynab.ApiClient(configuration) as api_client:
        categories_api = ynab.CategoriesApi(api_client)
        transactions_api = ynab.TransactionsApi(api_client)

        # Get all categories and build id->name and name->id maps
        try:
            cat_response = categories_api.get_categories(BUDGET_ID)
            category_names = []
            category_id_to_name = {}
            category_name_to_id = {}
            for group in cat_response.data.category_groups or []:
                if getattr(group, "deleted", False) or getattr(group, "hidden", False):
                    continue
                for cat in group.categories or []:
                    if getattr(cat, "deleted", False) or getattr(cat, "hidden", False):
                        continue
                    category_names.append(cat.name)
                    category_id_to_name[cat.id] = cat.name
                    category_name_to_id[cat.name] = cat.id
            print(f"Loaded {len(category_names)} categories from YNAB.")
        except ApiException as e:
            print(f"Error fetching categories: {e}")
            sys.exit(1)

        # Fetch all transactions in date range
        all_txs = []
        since = START_DATE
        while True:
            try:
                resp = transactions_api.get_transactions_by_account(
                    BUDGET_ID, ACCOUNT_ID, since_date=since
                )
                txs = resp.data.transactions or []
                for tx in txs:
                    if getattr(tx, "deleted", False):
                        continue
                    tx_date = getattr(tx, "var_date", None) or getattr(tx, "date", None)
                    if tx_date is None:
                        continue
                    if isinstance(tx_date, str):
                        try:
                            tx_date = datetime.strptime(tx_date[:10], "%Y-%m-%d").date()
                        except ValueError:
                            continue
                    if START_DATE <= tx_date <= END_DATE:
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
                if latest is None or latest >= END_DATE:
                    break
                since = (latest + timedelta(days=1)) if latest else END_DATE
            except ApiException as e:
                print(f"Error fetching transactions: {e}")
                sys.exit(1)

        print(f"Found {len(all_txs)} non-deleted transaction(s) in {START_DATE} to {END_DATE}.")

        # 1) Find and delete duplicates: same (amount, date) -> keep first, delete rest
        by_key = defaultdict(list)
        for tx in all_txs:
            tx_date = getattr(tx, "var_date", None) or getattr(tx, "date", None)
            if isinstance(tx_date, str):
                try:
                    tx_date = datetime.strptime(tx_date[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
            amt = getattr(tx, "amount", None)
            if amt is None:
                continue
            by_key[(amt, tx_date)].append(tx)

        to_delete = []
        for (amt, d), group in by_key.items():
            if len(group) > 1:
                to_delete.extend(group[1:])

        deleted_ids = set()
        for tx in to_delete:
            try:
                transactions_api.delete_transaction(BUDGET_ID, tx.id)
                d = getattr(tx, "var_date", None) or getattr(tx, "date", None)
                amt = getattr(tx, "amount", 0)
                print(f"  Deleted duplicate: {tx.id} ({d} {amt/1000:.2f})")
                deleted_ids.add(tx.id)
            except ApiException as e:
                print(f"  Failed to delete {tx.id}: {e}")

        if to_delete:
            print(f"Deleted {len(deleted_ids)} duplicate(s).")

        # 2) Verify categories with AI
        remaining = [tx for tx in all_txs if tx.id not in deleted_ids]
        
        # Skip split transactions
        items_to_check = []
        for tx in remaining:
            category_name = (getattr(tx, "category_name", None) or "").strip()
            if category_name == "Split":
                continue
            memo = (getattr(tx, "memo", None) or "") or (getattr(tx, "payee_name", None) or "")
            items_to_check.append({
                "id": tx.id,
                "memo": memo,
                "current_category": category_name,
                "tx": tx,
            })
        
        print(f"\nVerifying {len(items_to_check)} transaction categories with AI...")
        
        # Process in batches
        BATCH_SIZE = 25
        all_fixes = {}
        for i in range(0, len(items_to_check), BATCH_SIZE):
            batch = items_to_check[i:i + BATCH_SIZE]
            batch_items = [{"id": item["id"], "memo": item["memo"], "current_category": item["current_category"]} for item in batch]
            fixes = get_ai_categories(batch_items, category_names)
            all_fixes.update(fixes)
            print(f"  Checked {min(i + BATCH_SIZE, len(items_to_check))}/{len(items_to_check)}...")
        
        # Apply fixes
        fixed_count = 0
        for item in items_to_check:
            tx_id = item["id"]
            if tx_id in all_fixes:
                new_category = all_fixes[tx_id]
                if new_category not in category_name_to_id:
                    print(f"  Warning: Category '{new_category}' not found, skipping {tx_id}")
                    continue
                
                tx = item["tx"]
                old_category = item["current_category"]
                new_category_id = category_name_to_id[new_category]
                
                tx_date = getattr(tx, "var_date", None) or getattr(tx, "date", None)
                existing = ExistingTransaction(
                    account_id=tx.account_id,
                    var_date=tx_date,
                    amount=tx.amount,
                    payee_id=getattr(tx, "payee_id", None),
                    payee_name=getattr(tx, "payee_name", None),
                    category_id=new_category_id,
                    memo=getattr(tx, "memo", None),
                    cleared=getattr(tx, "cleared", None),
                    approved=getattr(tx, "approved", None),
                    flag_color=getattr(tx, "flag_color", None),
                    subtransactions=None,
                )
                try:
                    transactions_api.update_transaction(
                        BUDGET_ID, tx_id, PutTransactionWrapper(transaction=existing)
                    )
                    fixed_count += 1
                    memo_short = (item["memo"] or "")[:40]
                    print(f"  Fixed: '{old_category}' → '{new_category}' | {memo_short}...")
                except ApiException as e:
                    print(f"  Failed to update {tx_id}: {e}")

        if fixed_count:
            print(f"\nFixed {fixed_count} transaction category(ies).")
        else:
            print("\nAll categories verified correct; no changes needed.")
        
        print("Done.")


if __name__ == "__main__":
    main()
