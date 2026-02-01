#!/usr/bin/env python3
"""
Convert Amazon Order History Reporter (Chrome extension) CSV to YNAB import format.
Uses AI (Claude) to intelligently categorize products based on understanding what they are.
Outputs: Date, Payee, Memo, Amount, Category, OrderId.
Usage: python amazon_csv_to_ynab.py <amazon_export.csv> [--output out.csv]
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

try:
    from dotenv import load_dotenv
    # Load from script directory (handles iCloud/cwd issues)
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_script_dir, ".env"))
except ImportError:
    pass

import anthropic
import ynab
from ynab.rest import ApiException

PAYEE = "Amazon.ca"
DEFAULT_CATEGORY = "Uncategorized"

# Column detection candidates
DATE_COLUMNS = ["order.date", "order date", "order_date", "date", "order placed", "charged on"]
AMOUNT_COLUMNS = ["order.total", "order total", "order_total", "item total", "item.total", "total", "amount", "price"]
MEMO_COLUMNS = ["item.title", "item title", "item_title", "title", "product", "description", "item", "memo", "order.items"]
ORDER_ID_COLUMNS = ["order id", "order number", "order_id", "orderid"]
ORDER_TOTAL_COLUMNS = ["order.total", "order total", "order_total"]


def _find_column(row_dict: dict, candidates: list[str]) -> Optional[str]:
    keys_lower = {k.strip().lower(): k for k in row_dict.keys()}
    for c in candidates:
        if c.lower() in keys_lower:
            return keys_lower[c.lower()]
    for c in candidates:
        for k in keys_lower:
            if c.replace(" ", "").replace(".", "") in k.replace(" ", "").replace(".", ""):
                return keys_lower[k]
    return None


def _parse_date(s: str) -> Optional[str]:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    return None


def _parse_amount(s) -> Optional[float]:
    if s is None or (isinstance(s, str) and not s.strip()):
        return None
    s = str(s).strip().replace(",", "").replace("$", "").replace("CAD", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fetch_ynab_categories() -> list[str]:
    """Fetch category names from YNAB budget."""
    access_token = os.environ.get("YNAB_ACCESS_TOKEN")
    budget_id = os.environ.get("YNAB_BUDGET_ID")
    if not access_token or not budget_id:
        print("Warning: YNAB credentials not set; using default categories.")
        return [DEFAULT_CATEGORY]
    
    configuration = ynab.Configuration(access_token=access_token)
    categories = []
    try:
        with ynab.ApiClient(configuration) as api_client:
            categories_api = ynab.CategoriesApi(api_client)
            response = categories_api.get_categories(budget_id)
            for group in response.data.category_groups or []:
                if getattr(group, "deleted", False) or getattr(group, "hidden", False):
                    continue
                for cat in group.categories or []:
                    if getattr(cat, "deleted", False) or getattr(cat, "hidden", False):
                        continue
                    categories.append(cat.name)
    except ApiException as e:
        print(f"Warning: Could not fetch YNAB categories: {e}")
        return [DEFAULT_CATEGORY]
    
    return categories if categories else [DEFAULT_CATEGORY]


def _resolve_category(ai_response: str, valid_categories: list[str]) -> str:
    """Match AI response to a valid category (handles emoji/whitespace differences)."""
    ai_response = (ai_response or "").strip()
    if ai_response in valid_categories:
        return ai_response
    # Strip emojis and extra whitespace for fuzzy match
    import unicodedata
    def normalize(s: str) -> str:
        return "".join(c for c in s if unicodedata.category(c) != "So").strip().lower()
    ai_norm = normalize(ai_response)
    for cat in valid_categories:
        if normalize(cat) == ai_norm:
            return cat
        if ai_norm in normalize(cat) or normalize(cat) in ai_norm:
            return cat
    return DEFAULT_CATEGORY


def categorize_with_ai(items: list[dict], categories: list[str]) -> dict[int, str]:
    """
    Use Claude to intelligently categorize products.
    Returns: {index: category_name}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Warning: ANTHROPIC_API_KEY not set; all items will be Uncategorized.")
        return {i: DEFAULT_CATEGORY for i in range(len(items))}
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Build the prompt with items and categories
    category_list = "\n".join(f"- {cat}" for cat in categories)
    
    items_text = ""
    for i, item in enumerate(items):
        memo = item.get("memo", "")[:300]
        items_text += f"{i}. {memo}\n"
    
    prompt = f"""You are a budget categorization assistant. For each Amazon purchase below, determine the most appropriate budget category based on what the product actually is.

AVAILABLE CATEGORIES:
{category_list}

ITEMS TO CATEGORIZE:
{items_text}

INSTRUCTIONS:
1. Understand what each product actually is (not just keyword matching)
2. Consider the context:
   - Kids clothing, toys, books for children → "Kids Supplies"
   - Adult clothing, shoes, accessories → "Wardrobe"
   - Movie rentals, streaming → "Family Fun & Dates" or "Subscriptions (Monthly)" for recurring
   - Health supplements, vitamins for adults → "Medicine & Vitamins"
   - Books for personal reading (adult fiction/non-fiction) → owner's Fun Money category or appropriate
   - Light fixtures, sconces, bulbs → "Light Fixtures" if available, else "Home Maintenance & Decor"
   - Coffee tables, ottomans → "Coffee Table & Side Tables" if available, else "Home Maintenance & Decor"
   - Cleaning supplies, kitchenware, tools → "Home Maintenance & Decor"
   - Subscription services (Apple TV+, Prime Video ad-free, media apps) → "Subscriptions (Monthly)"
   - Gift cards → "Gifts & Giving"
   - Spiritual/Buddhist books → "Retreats"
   - Tech gadgets (chargers, mice, electronics for personal use) → "Matt's Fun Money 🤑" or appropriate
   - Women's personal items (lingerie, dresses, books for her) → "Wardrobe" or "Sheva's Fun Money 💸"
   - UGG slippers, shoes, footwear → "Wardrobe"
   - Beverages, water enhancers, drink mixes → "Groceries"
   - Phone tripods, selfie sticks (for fitness/vlogging) → "Fitness & Coaching" or "Matt's Fun Money 🤑"
   - Mixed orders (book + kids product) → pick the category of the higher-value item
3. For mixed orders (multiple items), pick the category of the highest-value or primary item
4. If truly uncertain, use "Uncategorized"

Return ONLY a JSON object mapping item index to category name.
CRITICAL: You MUST use the EXACT category name from the list above, including any emojis (e.g. "Matt's Fun Money 🤑" not "Matt's Fun Money").
Example:
{{"0": "Kids Supplies", "1": "Wardrobe", "2": "Matt's Fun Money 🤑"}}

JSON response:"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Parse the JSON response
        response_text = response.content[0].text.strip()
        # Handle potential markdown code blocks
        if response_text.startswith("```"):
            response_text = re.sub(r"```json?\s*", "", response_text)
            response_text = re.sub(r"```\s*$", "", response_text)
        
        result = json.loads(response_text)
        return {int(k): v for k, v in result.items()}
    
    except Exception as e:
        print(f"Warning: AI categorization failed: {e}")
        return {i: DEFAULT_CATEGORY for i in range(len(items))}


def main():
    parser = argparse.ArgumentParser(
        description="Convert Amazon Order History Reporter CSV to YNAB import format with AI categorization."
    )
    parser.add_argument("input_csv", help="Path to Amazon export CSV from Chrome extension")
    parser.add_argument("-o", "--output", help="Output CSV path (default: amazon_ynab_ready.csv)")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI categorization (leave Category as Uncategorized)")
    args = parser.parse_args()

    input_path = args.input_csv
    if not os.path.isfile(input_path):
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    output_path = args.output or "amazon_ynab_ready.csv"

    # Parse CSV
    rows_out = []
    date_col = amount_col = memo_col = order_id_col = order_total_col = None

    with open(input_path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if date_col is None:
                date_col = _find_column(row, DATE_COLUMNS)
                amount_col = _find_column(row, AMOUNT_COLUMNS)
                memo_col = _find_column(row, MEMO_COLUMNS)
                order_id_col = _find_column(row, ORDER_ID_COLUMNS)
                order_total_col = _find_column(row, ORDER_TOTAL_COLUMNS)
                if date_col:
                    print(f"Using date column: '{date_col}'")
                if amount_col:
                    print(f"Using amount column: '{amount_col}'")
                if memo_col:
                    print(f"Using memo column: '{memo_col}'")
                if order_id_col:
                    print(f"Using order id column: '{order_id_col}'")
                elif order_total_col:
                    print(f"Using order total for OrderId: '{order_total_col}'")
                if not date_col or not amount_col:
                    print("Error: Could not find date and amount columns. Available:", list(row.keys()))
                    sys.exit(1)

            date_str = _parse_date(row.get(date_col, ""))
            amount_val = _parse_amount(row.get(amount_col, ""))
            memo_str = (row.get(memo_col, "") or "").strip()[:500]

            if not date_str or amount_val is None:
                continue

            # YNAB: negative = outflow (spending), positive = inflow (refund)
            if amount_val > 0 and any(
                x in (memo_str or "").lower()
                for x in ["return", "refund", "reimbursement"]
            ):
                amount_ynab = amount_val
            else:
                amount_ynab = -abs(amount_val) if amount_val != 0 else 0

            if order_id_col and row.get(order_id_col, "").strip():
                order_id = str(row.get(order_id_col, "")).strip()
            elif order_total_col:
                order_total_val = _parse_amount(row.get(order_total_col, ""))
                order_id = f"{date_str}|{order_total_val}" if order_total_val is not None else ""
            else:
                order_id = ""
            
            rows_out.append({
                "Date": date_str,
                "Payee": PAYEE,
                "Memo": memo_str or f"Order {date_str}",
                "Amount": amount_ynab,
                "Category": "",  # Will be filled by AI
                "OrderId": order_id,
            })

    # Deduplicate by (date, amount, memo) - keep first
    seen = set()
    deduped = []
    for r in rows_out:
        key = (r["Date"], r["Amount"], r["Memo"][:100])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    print(f"Parsed {len(deduped)} unique transactions.")

    # AI Categorization
    if args.no_ai:
        print("Skipping AI categorization (--no-ai flag).")
        for r in deduped:
            r["Category"] = DEFAULT_CATEGORY
    else:
        print("Fetching YNAB categories...")
        categories = fetch_ynab_categories()
        print(f"Found {len(categories)} categories.")
        
        print("Categorizing with AI (this may take a moment)...")
        # Process in batches of 30 to avoid token limits
        BATCH_SIZE = 30
        for i in range(0, len(deduped), BATCH_SIZE):
            batch = deduped[i:i + BATCH_SIZE]
            items = [{"memo": r["Memo"]} for r in batch]
            categorizations = categorize_with_ai(items, categories)
            
            for j, r in enumerate(batch):
                ai_cat = categorizations.get(j, DEFAULT_CATEGORY)
                r["Category"] = _resolve_category(ai_cat, categories)
            
            print(f"  Categorized {min(i + BATCH_SIZE, len(deduped))}/{len(deduped)} items...")

    # Write output
    with open(output_path, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Payee", "Memo", "Amount", "Category", "OrderId"])
        writer.writeheader()
        writer.writerows(deduped)

    print(f"\nWrote {len(deduped)} transactions to {output_path}")
    
    # Summary by category
    from collections import Counter
    cat_counts = Counter(r["Category"] for r in deduped)
    print("\nCategory breakdown:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    
    print("\nNext: Review the CSV, edit Category if needed, then run:")
    print(f"  YNAB_CSV_FILE={output_path} python3 ynab_import.py")


if __name__ == "__main__":
    main()
