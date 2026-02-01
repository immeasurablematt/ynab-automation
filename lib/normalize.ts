const PAYEE = "Amazon.ca";
const DEFAULT_CATEGORY = "Uncategorized";

const CATEGORY_KEYWORDS: [string, string[]][] = [
  ["Kids Supplies", ["kid", "baby", "toddler", "diaper", "crib", "stroller", "book", "toy", "magnetic tiles", "pat-a-cake", "oobleck", "bartholomew"]],
  ["Home Maintenance & Decor", ["filter", "furniture", "ottoman", "coffee table", "light bulb", "dimmer", "screwdriver", "scissors", "splatter", "bed rail"]],
  ["Personal Care", ["cerave", "baby wash", "shampoo", "skincare", "makeup", "soap"]],
  ["Wardrobe", ["slipper", "ugg", "shoes", "clothes", "gaiters"]],
  ["Subscriptions (Monthly)", ["prime video", "ad free", "subscription", "appstore", "vimu"]],
  ["Gifts & Giving", ["gift card", "egift", "gingerbread"]],
  ["Retreats", ["buddhism", "vajrayana", "dangerous friend", "dharma"]],
  ["Fitness & Coaching", ["gym", "phone holder", "fitness", "coaching"]],
  ["Groceries", ["glad", "garbage bag", "grocer"]],
];

const DATE_COLS = ["order.date", "order date", "order_date", "date", "order placed", "charged on"];
const AMOUNT_COLS = ["order.total", "order total", "order_total", "item total", "item.total", "total", "amount", "price"];
const MEMO_COLS = ["item.title", "item title", "item_title", "title", "product", "description", "item", "memo", "order.items"];
const ORDER_ID_COLS = ["order id", "order number", "order_id", "orderid"];
const ORDER_TOTAL_COLS = ["order.total", "order total", "order_total"];

function findColumn(keys: string[], candidates: string[]): string | null {
  const lower = Object.fromEntries(keys.map((k) => [k.trim().toLowerCase(), k]));
  for (const c of candidates) {
    if (lower[c.toLowerCase()]) return lower[c.toLowerCase()];
  }
  for (const c of candidates) {
    const norm = c.replace(/[\s.]/g, "");
    for (const k of Object.keys(lower)) {
      if (k.replace(/[\s.]/g, "").includes(norm)) return lower[k];
    }
  }
  return null;
}

function parseDate(s: string): string | null {
  const x = String(s || "").trim();
  if (!x) return null;
  const fmts = ["yyyy-mm-dd", "mm/dd/yyyy", "m/d/yyyy"];
  const m = x.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  const m2 = x.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (m2) return `${m2[3]}-${m2[1].padStart(2, "0")}-${m2[2].padStart(2, "0")}`;
  return null;
}

function parseAmount(s: unknown): number | null {
  if (s == null || (typeof s === "string" && !s.trim())) return null;
  const x = String(s).replace(/[,$CAD]/g, "").trim();
  const n = parseFloat(x);
  return isNaN(n) ? null : n;
}

function categorize(memo: string): string {
  const m = (memo || "").toLowerCase();
  for (const [cat, keywords] of CATEGORY_KEYWORDS) {
    for (const kw of keywords) {
      if (m.includes(kw.toLowerCase())) return cat;
    }
  }
  return DEFAULT_CATEGORY;
}

export interface CsvRow {
  Date: string;
  Payee: string;
  Memo: string;
  Amount: number;
  Category: string;
  OrderId?: string;
}

import { parse } from "csv-parse/sync";

export function parseCsv(csvText: string): Record<string, string>[] {
  const rows = parse(csvText, {
    columns: true,
    skip_empty_lines: true,
    relax_column_count: true,
    bom: true,
  }) as Record<string, string>[];
  return rows;
}

export function normalizeAmazonCsv(csvText: string, noCategory = false): CsvRow[] {
  const rows = parseCsv(csvText);
  if (rows.length === 0) return [];

  const keys = Object.keys(rows[0]);
  const dateCol = findColumn(keys, DATE_COLS);
  const amountCol = findColumn(keys, AMOUNT_COLS);
  const memoCol = findColumn(keys, MEMO_COLS);
  const orderIdCol = findColumn(keys, ORDER_ID_COLS);
  const orderTotalCol = findColumn(keys, ORDER_TOTAL_COLS);

  if (!dateCol || !amountCol) return [];

  const out: CsvRow[] = [];
  const seen = new Set<string>();

  for (const row of rows) {
    const dateStr = parseDate(row[dateCol] ?? "");
    const amountVal = parseAmount(row[amountCol]);
    const memoStr = (row[memoCol ?? ""] ?? "").trim().slice(0, 500);

    if (!dateStr || amountVal == null) continue;

    const isRefund = /return|refund|reimbursement/i.test(memoStr) && amountVal > 0;
    const amountYnab = isRefund ? amountVal : -Math.abs(amountVal);

    const key = `${dateStr}|${amountYnab}|${memoStr.slice(0, 100)}`;
    if (seen.has(key)) continue;
    seen.add(key);

    let orderId: string | undefined;
    if (orderIdCol && (row[orderIdCol] ?? "").toString().trim()) {
      orderId = (row[orderIdCol] ?? "").toString().trim();
    } else if (orderTotalCol) {
      const orderTotal = parseAmount(row[orderTotalCol]);
      orderId = orderTotal != null ? `${dateStr}|${orderTotal}` : undefined;
    }

    const category = noCategory ? "" : categorize(memoStr);
    out.push({
      Date: dateStr,
      Payee: PAYEE,
      Memo: memoStr || `Order ${dateStr}`,
      Amount: amountYnab,
      Category: category,
      ...(orderId != null ? { OrderId: orderId } : {}),
    });
  }

  return out;
}

export function ynabReadyToJson(csvText: string): CsvRow[] {
  const rows = parseCsv(csvText);
  const out: CsvRow[] = [];
  const hasOrderId = rows.length > 0 && "OrderId" in rows[0];
  for (const row of rows) {
    const dateStr = (row.Date ?? "").trim();
    const amountStr = (row.Amount ?? "0").replace(/,/g, "");
    let amount = parseFloat(amountStr);
    if (!dateStr || isNaN(amount)) continue;
    if (amount > 0) amount = -amount;
    const csvRow: CsvRow = {
      Date: dateStr,
      Payee: (row.Payee ?? "Amazon.ca").trim(),
      Memo: (row.Memo ?? "").trim().slice(0, 500),
      Amount: amount,
      Category: (row.Category ?? "Uncategorized").trim(),
    };
    if (hasOrderId && (row.OrderId ?? "").toString().trim()) {
      csvRow.OrderId = (row.OrderId ?? "").toString().trim();
    }
    out.push(csvRow);
  }
  return out;
}

export function dedupeYnabReadyRows(rows: CsvRow[]): CsvRow[] {
  const seen = new Set<string>();
  const out: CsvRow[] = [];
  for (const row of rows) {
    const milli = Math.round(row.Amount * 1000);
    const key = `${row.Date}|${milli}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}
