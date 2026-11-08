-- Automatisches Preis-Tracking pro Trip ein-/ausschaltbar; Trips loeschbar (nur Admin).
alter table public.trips add column tracking_enabled boolean not null default true;

create policy trips_delete_admin on public.trips
  for delete to authenticated using (public.is_admin());
