// Number and time formatting. Indian digit grouping, and India time wherever a clock time is shown.

const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const numFmt = (d: number) => new Intl.NumberFormat("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const clockFmt = new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Asia/Kolkata" });
const dayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

export const dash = "–";

export function inr(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return dash;
  return (n < 0 ? "-₹" : "₹") + inrFmt.format(Math.abs(n));
}

export function signedInr(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return dash;
  if (n === 0) return "₹0";
  return (n > 0 ? "+₹" : "-₹") + inrFmt.format(Math.abs(n));
}

export function price(n: number | null | undefined, d = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return dash;
  return numFmt(d).format(n);
}

export function signedR(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return dash;
  return (n > 0 ? "+" : n < 0 ? "-" : "") + Math.abs(n).toFixed(2) + "R";
}

export function percent(n: number | null | undefined, d = 0): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return dash;
  return n.toFixed(d) + "%";
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return dash;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? dash : clockFmt.format(t).toLowerCase();
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return dash;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? dash : dayFmt.format(t);
}

/** Only http(s) links are ever turned into anchors. */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const u = new URL(value);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}
