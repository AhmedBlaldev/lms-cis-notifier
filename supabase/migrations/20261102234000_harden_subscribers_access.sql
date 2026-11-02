alter table public.subscribers add column if not exists lms_password_ciphertext text;
update public.subscribers set lms_password_ciphertext = null;
alter table public.subscribers drop column if exists lms_password;

drop policy if exists "Allow public delete for unsubscribe" on public.subscribers;
drop policy if exists "Allow public insert on subscribers" on public.subscribers;
drop policy if exists "Allow public update for upsert" on public.subscribers;

revoke all on table public.subscribers from anon, authenticated;
revoke all on sequence public.subscribers_id_seq from anon, authenticated;
