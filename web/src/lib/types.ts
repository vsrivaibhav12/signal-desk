// Shapes of the published files that are not part of the generated contract (written by other services; read defensively).
export interface IntelImpact {
  asset?: string;
  label?: string;
  bias?: string;
  why?: string;
}

export interface IntelEvent {
  id?: string;
  title?: string;
  link?: string;
  source?: string;
  published_at?: string;
  category?: string;
  impact_score?: number;
  severity?: string;
  confidence?: string;
  horizon?: string;
  summary?: string;
  impacts?: IntelImpact[];
  confirm?: string[];
  market_confirmation?: string;
}

export interface Intel {
  schema: number;
  status?: { last_scan?: string; running?: boolean; worker_mode?: string; analysis_mode?: string };
  events?: IntelEvent[];
  disclaimer?: string;
}

// Parts of live.json that the contract leaves loosely typed (they pass through from the engine); read defensively.
export interface RegimeMarket {
  name: string;
  price: number | null;
  chg: number | null;
  adx: number | null;
  label: string;
  trend: string;
  vwap: string;
  range: number | null;
  squeeze: boolean;
}

export interface Regime {
  asof?: string;
  markets?: RegimeMarket[];
  breadth?: { stocks?: number; trending?: number | null; ranging?: number | null; above_vwap?: number | null; up_on_day?: number | null };
}

export interface Mover {
  name: string;
  dir: number;
  price: number;
  move: number;
  vol: number | null;
  held: number;
  genuine: boolean;
  since?: string | null;
}

export interface ResultEvent {
  symbol: string;
  company?: string;
  date: string;
  purpose?: string;
}

export interface ResultsPublic {
  today?: ResultEvent[];
  last?: ResultEvent[];
  next?: ResultEvent[];
  later?: ResultEvent[];
  ok?: boolean;
  fetched_at?: string | null;
}

export interface LeadersPublic {
  ok?: boolean;
  asof?: string;
  universe?: number;
  n_leaders?: number;
  breadth?: { above50: number; above200: number; advancing: number; bear_phase: boolean };
}

export interface Tile {
  id?: string;
  name?: string;
  book?: string;
  note?: string;
  price?: number | null;
  vwap?: number | null;
  prev_close?: number | null;
  in_trade?: boolean;
  done?: boolean;
}
