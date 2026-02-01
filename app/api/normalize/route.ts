import { NextResponse } from "next/server";
import { normalizeAmazonCsv } from "@/lib/normalize";

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    const file = formData.get("file") as File | null;
    if (!file) {
      return NextResponse.json({ error: "No file uploaded" }, { status: 400 });
    }
    const text = await file.text();
    const rows = normalizeAmazonCsv(text);
    const csv = [
      "Date,Payee,Memo,Amount,Category,OrderId",
      ...rows.map((r) =>
        [r.Date, r.Payee, `"${(r.Memo || "").replace(/"/g, '""')}"`, r.Amount, r.Category, r.OrderId ?? ""].join(",")
      ),
    ].join("\n");
    return new NextResponse(csv, {
      headers: {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=amazon_ynab_ready.csv",
      },
    });
  } catch (e) {
    console.error(e);
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Normalize failed" },
      { status: 500 }
    );
  }
}
