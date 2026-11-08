-- Initiales Schema: Trips, Preisbeobachtungen, Empfehlungen, API-Nutzung, Cache.

create table public.app_admins (
  email text primary key
);

create table public.trips (
  id uuid primary key default gen_random_uuid(),
  name text unique not null,
  profile jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.price_observations (
  id bigint generated always as identity primary key,
  trip_id uuid not null references public.trips(id) on delete cascade,
  option_key text not null,
  observed_at timestamptz not null,
  out_date date,
  ret_date date,
  out_origin text,
  out_destination text,
  ret_origin text,
  ret_destination text,
  price numeric(10,2) not null,
  airlines text[],
  out_stops int,
  ret_stops int,
  out_departure timestamp,
  ret_departure timestamp,
  source text
);
create index price_observations_trip_option_observed_idx
  on public.price_observations (trip_id, option_key, observed_at);
create index price_observations_trip_observed_idx
  on public.price_observations (trip_id, observed_at);

create table public.recommendations (
  id bigint generated always as identity primary key,
  trip_id uuid not null references public.trips(id) on delete cascade,
  created_at timestamptz not null default now(),
  payload jsonb not null
);
create index recommendations_trip_created_idx
  on public.recommendations (trip_id, created_at desc);

create table public.api_usage (
  id bigint generated always as identity primary key,
  provider text not null,
  kind text,
  called_at timestamptz not null default now()
);
create index api_usage_provider_called_idx
  on public.api_usage (provider, called_at);

create table public.search_cache (
  key text primary key,
  value jsonb not null,
  fetched_at timestamptz not null default now()
);

-- updated_at-Trigger fuer trips
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trips_set_updated_at
  before update on public.trips
  for each row execute function public.set_updated_at();

-- Admin-Pruefung (E-Mail aus dem JWT gegen app_admins)
create function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.app_admins
    where email = (select auth.jwt() ->> 'email')
  );
$$;

revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated, service_role;

-- RLS auf allen Tabellen
alter table public.app_admins enable row level security;
alter table public.trips enable row level security;
alter table public.price_observations enable row level security;
alter table public.recommendations enable row level security;
alter table public.api_usage enable row level security;
alter table public.search_cache enable row level security;

-- Policies (nur Rolle authenticated); app_admins und search_cache: nur Service-Role
create policy trips_select_admin on public.trips
  for select to authenticated using (public.is_admin());
create policy trips_insert_admin on public.trips
  for insert to authenticated with check (public.is_admin());
create policy trips_update_admin on public.trips
  for update to authenticated using (public.is_admin()) with check (public.is_admin());

create policy price_observations_select_admin on public.price_observations
  for select to authenticated using (public.is_admin());
create policy recommendations_select_admin on public.recommendations
  for select to authenticated using (public.is_admin());
create policy api_usage_select_admin on public.api_usage
  for select to authenticated using (public.is_admin());
