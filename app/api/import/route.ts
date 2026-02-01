import { NextResponse } from "next/server";
import * as ynab from "ynab";
import { ynabReadyToJson, dedupeYnabReadyRows } from "@/lib/normalize";

const DAYS_TOLERANCE = parseInt(process.env.YNAB_DUPLICATE_DAYS || "5", 10);
const PAGE_SIZE = 500;

export async function POST(request: Request) {
  const token = process.env.YNAB_ACCESS_TOKEN;
  const budgetId = process.env.YNAB_BUDGET_ID;
  const accountId = process.env.YNAB_ACCOUNT_ID;

  if (!token || !budgetId || !accountId) {
    return NextResponse.json(
      { error: "Missing YNAB_ACCESS_TOKEN, YNAB_BUDGET_ID, or YNAB_ACCOUNT_ID in environment" },
      { status: 500 }
    );
  }

  try {
    const formData = await request.formData();
    const file = formData.get("file") as File | null;
    if (!file) {
      return NextResponse.json({ error: "No file uploaded" }, { status: 400 });
    }

    const text = await file.text();
    const parsed = ynabReadyToJson(text);
    const hasOrderId = parsed.some((r) => (r.OrderId ?? "").trim() !== "");
    const deduped = hasOrderId ? parsed : dedupeYnabReadyRows(parsed);
    const csvRows = deduped.filter((r) => Math.round(r.Amount * 1000) !== 0);
    const skippedWithinFile = parsed.length - deduped.length;
    const skippedZeroAmount = deduped.length - csvRows.length;

    if (csvRows.length === 0) {
      return NextResponse.json({
        imported: 0,
        skippedDuplicates: 0,
        skippedWithinFile,
        skippedZeroAmount,
        error: parsed.length === 0 ? "No valid rows in CSV" : "All rows were duplicates or zero amount",
      });
    }

    const api = new ynab.API(token);

    // Fetch categories
    const catRes = await api.categories.getCategories(budgetId);
    const categoryIdMap: Record<string, string> = {};
    for (const group of catRes.data.category_groups || []) {
      for (const cat of group.categories || []) {
        if (!cat.deleted && !cat.hidden) {
          categoryIdMap[(cat.name || "").toLowerCase()] = cat.id!;
        }
      }
    }

    // Min date for duplicate check
    const minDate = csvRows.reduce(
      (a, r) => (r.Date < a ? r.Date : a),
      csvRows[0].Date
    );
    let sinceDateStr = new Date(minDate).toISOString().slice(0, 10);
    const sinceStart = new Date(minDate);
    sinceStart.setDate(sinceStart.getDate() - DAYS_TOLERANCE);
    sinceDateStr = sinceStart.toISOString().slice(0, 10);

    const existingByAmount: Record<number, string[]> = {};
    while (true) {
      const existingRes = await api.transactions.getTransactionsByAccount(
        budgetId,
        accountId,
        sinceDateStr
      );
      const txs = existingRes.data.transactions || [];
      for (const tx of txs) {
        if (tx.deleted || tx.amount == null || !tx.date) continue;
        const dt = typeof tx.date === "string" ? tx.date.slice(0, 10) : "";
        if (!dt) continue;
        if (!existingByAmount[tx.amount]) existingByAmount[tx.amount] = [];
        existingByAmount[tx.amount].push(dt);
      }
      if (txs.length < PAGE_SIZE || txs.length === 0) break;
      let latest = "";
      for (const tx of txs) {
        const d = typeof tx.date === "string" ? tx.date.slice(0, 10) : "";
        if (d && d > latest) latest = d;
      }
      if (!latest) break;
      const next = new Date(latest);
      next.setDate(next.getDate() + 1);
      sinceDateStr = next.toISOString().slice(0, 10);
    }

    function isDuplicate(importDate: string, amountMilli: number): boolean {
      const dates = existingByAmount[amountMilli];
      if (!dates) return false;
      const imp = new Date(importDate);
      for (const d of dates) {
        const ex = new Date(d);
        const diff = Math.abs((imp.getTime() - ex.getTime()) / (1000 * 60 * 60 * 24));
        if (diff <= DAYS_TOLERANCE) return true;
      }
      return false;
    }

    // Group by OrderId when present; else each row is its own group
    const groups = new Map<string, typeof csvRows>();
    for (const row of csvRows) {
      const key = row.OrderId?.trim()
        ? row.OrderId.trim()
        : `${row.Date}|${Math.round(row.Amount * 1000)}|${(row.Memo || "").slice(0, 60)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(row);
    }

    const transactions: ynab.NewTransaction[] = [];
    let skippedDuplicates = 0;

    for (const [, groupRows] of groups) {
      const first = groupRows[0];
      const dateStr = new Date(first.Date).toISOString().slice(0, 10);
      const totalMilli = groupRows.reduce((s, r) => s + Math.round(r.Amount * 1000), 0);
      if (totalMilli === 0) continue;
      if (isDuplicate(first.Date, totalMilli)) {
        skippedDuplicates++;
        continue;
      }

      const payee = first.Payee?.trim() || "Amazon.ca";
      const memo = first.Memo?.trim().slice(0, 500) || null;

      // Aggregate by category (category_id -> sum milli)
      const catAmounts = new Map<string | null, number>();
      for (const row of groupRows) {
        const cid =
          row.Category && categoryIdMap[row.Category.toLowerCase()]
            ? categoryIdMap[row.Category.toLowerCase()]
            : null;
        const amt = Math.round(row.Amount * 1000);
        catAmounts.set(cid, (catAmounts.get(cid) ?? 0) + amt);
      }

      const importId = `YNAB:${totalMilli}:${dateStr}:1`;

      if (catAmounts.size === 1) {
        const onlyCatId = catAmounts.keys().next().value ?? null;
        transactions.push({
          account_id: accountId,
          date: dateStr,
          amount: totalMilli,
          payee_name: payee,
          memo,
          category_id: onlyCatId,
          cleared: "uncleared",
          approved: false,
          import_id: importId,
        });
      } else {
        const subtransactions = Array.from(catAmounts.entries()).map(([category_id, amount]) => ({
          amount,
          category_id,
          memo: null as string | null,
        }));
        transactions.push({
          account_id: accountId,
          date: dateStr,
          amount: totalMilli,
          payee_name: payee,
          memo: memo || `Order ${dateStr} (split)`,
          category_id: null,
          cleared: "uncleared",
          approved: false,
          import_id: importId,
          subtransactions,
        });
      }
    }

    if (transactions.length === 0) {
      return NextResponse.json({
        imported: 0,
        skippedDuplicates,
        skippedWithinFile,
        skippedZeroAmount,
        existingLoaded: Object.values(existingByAmount).flat().length,
      });
    }

    const createRes = await api.transactions.createTransaction(budgetId, {
      transactions,
    });

    const created = createRes.data.transactions?.length ?? 0;
    const apiDuplicates = createRes.data.duplicate_import_ids?.length ?? 0;

    return NextResponse.json({
      imported: created,
      skippedDuplicates,
      skippedWithinFile,
      skippedZeroAmount,
      apiDuplicates,
      existingLoaded: Object.values(existingByAmount).flat().length,
    });
  } catch (e: unknown) {
    const err = e as { error?: { detail?: string }; message?: string };
    const msg =
      err?.error?.detail || err?.message || (e instanceof Error ? e.message : "Import failed");
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
