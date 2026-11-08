export interface Leg {
  origin: string;
  destination: string;
  departure: string;
  arrival: string;
  duration_min: number;
  stops: number;
  airlines: string[];
  route: string[];
}
export interface TripOption {
  outbound: Leg;
  inbound: Leg;
  price: number;
  source: string;
  booking_tokens: string[];
  effective_cost: number | null;
  penalties: Record<string, number>;
  nights: number;
  key: string;
}
export type Action = "BUY_NOW" | "WAIT" | "WATCH";
export interface TimingAdvice {
  action: Action;
  decide_by: string | null;
  target_price: number | null;
  confidence: string;
  basis?: string | null;
  reasons: string[];
  source: string;
}
export interface Recommendation {
  trip_name: string;
  created_at: string;
  best: TripOption;
  cheapest: TripOption;
  alternatives: TripOption[];
  explanation: string;
  timing_rules: TimingAdvice;
  timing_llm: TimingAdvice | null;
  api_calls_used: number;
  api_calls_remaining_month: number | null;
  hints?: string[];
}
export interface Preferences {
  avoid_early_departure: boolean;
  avoid_late_arrival: boolean;
  avoid_stops: boolean;
  avoid_long_duration: boolean;
  avoid_length_deviation: boolean;
}
export interface Profile {
  name: string;
  origins: string[];
  destinations: string[];
  earliest_departure: string;
  latest_return: string;
  nights: number;
  min_nights: number;
  max_nights: number;
  flex_days: number;
  allow_open_jaw: boolean;
  preferences: Preferences;
  currency: string;
}
export interface Trip {
  id: string;
  name: string;
  profile: Profile;
}
export interface Observation {
  option_key: string;
  observed_at: string;
  price: number;
}
