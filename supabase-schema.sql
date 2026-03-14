-- ============================================
-- SHELFOWL.AI - Supabase Schema
-- New separate Supabase project
-- ============================================

-- 1. STORES
create table stores (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  address text,
  owner_id uuid references auth.users(id),
  created_at timestamptz default now()
);

-- 2. CAMERAS
create table cameras (
  id uuid primary key default gen_random_uuid(),
  store_id uuid references stores(id) on delete cascade,
  name text not null,
  location text not null,
  rtsp_url text,
  cam_number int,
  is_online boolean default true,
  created_at timestamptz default now()
);

-- 3. ZONES
create table zones (
  id uuid primary key default gen_random_uuid(),
  store_id uuid references stores(id) on delete cascade,
  camera_id uuid references cameras(id),
  name text not null,
  zone_type text not null check (zone_type in ('loitering','high_risk','exit_monitor')),
  severity text default 'medium' check (severity in ('high','medium','low')),
  coordinates text,
  created_at timestamptz default now()
);

-- 4. SHELF ALERTS
create table shelf_alerts (
  id uuid primary key default gen_random_uuid(),
  store_id uuid,
  severity text not null check (severity in ('high','medium','low')),
  type text not null,
  message text not null,
  zone_name text,
  camera_name text,
  confidence real,
  duration_sec real,
  is_resolved boolean default false,
  resolved_at timestamptz,
  snapshot_url text,
  created_at timestamptz default now()
);

-- ============================================
-- SEED DATA — update store_id after inserting store
-- ============================================

-- Insert your store (run this first, note the returned ID)
insert into stores (name, address)
values ('ShelfOwl Store #1', '123 Main St, Your City');

-- Insert cameras (update store_id with value from above)
-- insert into cameras (store_id, name, location, cam_number, rtsp_url) values
--   ('<your-store-id>', 'Camera 1', 'Entrance',     1, 'rtsp://...'),
--   ('<your-store-id>', 'Camera 7', 'Main Aisle',   2, 'rtsp://...'),
--   ('<your-store-id>', 'Camera 3', 'Register',     3, 'rtsp://...'),
--   ('<your-store-id>', 'Camera 4', 'Back Storage', 4, 'rtsp://...');

-- ============================================
-- DISABLE RLS (for development)
-- ============================================
alter table stores       disable row level security;
alter table cameras      disable row level security;
alter table zones        disable row level security;
alter table shelf_alerts disable row level security;

-- ============================================
-- REALTIME
-- ============================================
alter publication supabase_realtime add table shelf_alerts;
alter publication supabase_realtime add table cameras;
