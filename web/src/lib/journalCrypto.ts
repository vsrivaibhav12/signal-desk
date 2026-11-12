// Reads the encrypted journal in the browser. The format is written by engine/journal.py:
// AES-256-GCM, key = PBKDF2-HMAC-SHA256(passphrase, salt, iter), the authenticated data below, ciphertext with the tag at its end.
// The passphrase is used once and never stored.

export interface Envelope {
  v: number;
  kdf: string;
  iter: number;
  salt: string;
  nonce: string;
  ct: string;
}

export interface JournalEntry {
  id: string;
  t: string;
  took: boolean;
  name: string;
  desk: string;
  source: string;
  symbol: string;
  action: string;
  grade: string | null;
  contract: string | null;
  paper_entry: number | null;
  stop: number | null;
  target: number | null;
  fill_price: number | null;
  fill_qty: number | null;
  slippage_pct: number | null;
}

export interface JournalDoc {
  schema?: number;
  entries: JournalEntry[];
}

const AAD = new TextEncoder().encode("signal-desk-journal-v1");
const MIN_ITER = 100_000;
const MAX_ITER = 1_000_000;          // a hostile file must not be able to make the page grind

export class JournalError extends Error {}

function bytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function isEnvelope(x: unknown): x is Envelope {
  const e = x as Envelope;
  return !!e && typeof e === "object" && e.v === 1 && e.kdf === "pbkdf2-sha256" && Number.isInteger(e.iter)
    && typeof e.salt === "string" && typeof e.nonce === "string" && typeof e.ct === "string";
}

export async function decryptJournal(passphrase: string, envelope: unknown, subtle: SubtleCrypto = globalThis.crypto.subtle): Promise<JournalDoc> {
  if (!isEnvelope(envelope)) throw new JournalError("This is not a Signal Desk journal.");
  if (envelope.iter < MIN_ITER || envelope.iter > MAX_ITER) throw new JournalError("The journal uses unsupported key settings.");
  try {
    const material = await subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    const key = await subtle.deriveKey(
      { name: "PBKDF2", salt: bytes(envelope.salt), iterations: envelope.iter, hash: "SHA-256" },
      material, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const plain = await subtle.decrypt({ name: "AES-GCM", iv: bytes(envelope.nonce), additionalData: AAD }, key, bytes(envelope.ct));
    const doc = JSON.parse(new TextDecoder().decode(plain)) as JournalDoc;
    if (!doc || !Array.isArray(doc.entries)) throw new JournalError("The journal is damaged.");
    return doc;
  } catch (err) {
    if (err instanceof JournalError) throw err;
    throw new JournalError("Wrong passphrase, or the journal is damaged.");
  }
}

export interface JournalStats {
  took: number;
  skipped: number;
  withFill: number;
  /** Mean adverse slippage in percent of the paper price across entries that have a fill; null if there are none. */
  meanSlippagePct: number | null;
}

export function journalStats(entries: JournalEntry[]): JournalStats {
  const took = entries.filter((e) => e.took);
  const slips = took.map((e) => e.slippage_pct).filter((n): n is number => typeof n === "number" && Number.isFinite(n));
  return {
    took: took.length,
    skipped: entries.length - took.length,
    withFill: slips.length,
    meanSlippagePct: slips.length ? Math.round((slips.reduce((a, b) => a + b, 0) / slips.length) * 1000) / 1000 : null,
  };
}
