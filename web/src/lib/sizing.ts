// A calculator for sizing a swing position by hand. Nothing is sent anywhere; the numbers stay in the browser.

export interface SizeInput {
  capital: number;
  riskPct: number;
  entry: number;
  stopPct: number;
}

export interface SizeResult {
  qty: number;
  /** Rupees lost if the stop is hit (before costs and gaps). */
  risk: number;
  /** Rupees invested. */
  value: number;
  /** True when the capital, not the risk rule, limited the quantity. */
  limitedByCapital: boolean;
}

/**
 * Whole shares such that hitting the stop loses `riskPct` of `capital`, without spending more than the capital (no leverage assumed).
 * Anything invalid gives zero shares rather than a guess.
 */
export function swingSize({ capital, riskPct, entry, stopPct }: SizeInput): SizeResult {
  const ok = [capital, riskPct, entry, stopPct].every((n) => Number.isFinite(n) && n > 0) && riskPct <= 100 && stopPct < 100;
  if (!ok) return { qty: 0, risk: 0, value: 0, limitedByCapital: false };
  const perShareRisk = entry * (stopPct / 100);
  const byRisk = Math.floor((capital * (riskPct / 100)) / perShareRisk);
  const byCapital = Math.floor(capital / entry);
  const qty = Math.max(0, Math.min(byRisk, byCapital));
  return { qty, risk: Math.round(qty * perShareRisk * 100) / 100, value: Math.round(qty * entry * 100) / 100, limitedByCapital: byCapital < byRisk };
}
