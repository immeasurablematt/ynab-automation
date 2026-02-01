#!/usr/bin/env python3
"""Revert transactions mistakenly set to Stuff I Forgot / Zepbound back to Uncategorized."""
import os
import sys
from datetime import date

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import ynab
from ynab.rest import ApiException
from ynab.models.existing_transaction import ExistingTransaction
from ynab.models.put_transaction_wrapper import PutTransactionWrapper

BUDGET_ID = os.environ.get("YNAB_BUDGET_ID")
ACCOUNT_ID = os.environ.get("YNAB_ACCOUNT_ID")
ACCESS_TOKEN = os.environ.get("YNAB_ACCESS_TOKEN")

REVERT_CATEGORIES = ["Stuff I Forgot to Budget For", "Zepbound"]


def main():
    configuration = ynab.Configuration(access_token=ACCESS_TOKEN)
    with ynab.ApiClient(configuration) as api_client:
        cat_api = ynab.CategoriesApi(api_client)
        tx_api = ynab.TransactionsApi(api_client)

        resp = cat_api.get_categories(BUDGET_ID)
        uncat_id = None
        for g in resp.data.category_groups or []:
            for c in g.categories or []:
                if (c.name or "").strip() == "Uncategorized":
                    uncat_id = c.id
                    break
        if not uncat_id:
            print("Uncategorized category not found")
            sys.exit(1)

        resp = tx_api.get_transactions_by_account(BUDGET_ID, ACCOUNT_ID, since_date=date(2025, 12, 1))
        reverted = 0
        for tx in resp.data.transactions or []:
            if getattr(tx, "deleted", False):
                continue
            cat = (getattr(tx, "category_name") or "").strip()
            if cat not in REVERT_CATEGORIES:
                continue
            ex = ExistingTransaction(
                account_id=tx.account_id,
                var_date=getattr(tx, "var_date") or getattr(tx, "date"),
                amount=tx.amount,
                payee_id=getattr(tx, "payee_id", None),
                payee_name=getattr(tx, "payee_name", None),
                category_id=uncat_id,
                memo=getattr(tx, "memo", None),
                cleared=getattr(tx, "cleared", None),
                approved=getattr(tx, "approved", None),
                flag_color=getattr(tx, "flag_color", None),
                subtransactions=None,
            )
            try:
                tx_api.update_transaction(BUDGET_ID, tx.id, PutTransactionWrapper(transaction=ex))
                reverted += 1
                print(f"  Reverted: {tx.id} -> Uncategorized")
            except ApiException as e:
                print(f"  Failed {tx.id}: {e}")
        print(f"Reverted {reverted} transaction(s) to Uncategorized.")


if __name__ == "__main__":
    main()
