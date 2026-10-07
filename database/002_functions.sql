-- Funciones que necesita el backend (atomicidad que PostgREST no ofrece por sí solo).

-- Suma un intento fallido de login y bloquea la cuenta al llegar al máximo. Atómica.
create or replace function register_failed_login(p_user_id uuid, p_max integer)
returns table(attempts integer, locked boolean)
language plpgsql
as $$
declare
  v_attempts integer;
begin
  update users
     set failed_login_attempts = failed_login_attempts + 1
   where id = p_user_id
  returning failed_login_attempts into v_attempts;

  if v_attempts is null then
    return query select 0, false;
  elsif v_attempts >= p_max then
    update users set status = 'LOCKED' where id = p_user_id and status in ('ACTIVE', 'INVITED');
    return query select v_attempts, true;
  else
    return query select v_attempts, false;
  end if;
end;
$$;

-- Siguiente número de una secuencia documental (códigos CLI-00001, series de comprobantes…). Atómica.
create or replace function next_sequence(p_type text, p_year integer, p_series text default '')
returns bigint
language plpgsql
as $$
declare
  v bigint;
begin
  insert into document_sequences (document_type, fiscal_year, series, last_value)
  values (p_type, p_year, coalesce(p_series, ''), 1)
  on conflict (document_type, fiscal_year, series)
  do update set last_value = document_sequences.last_value + 1
  returning last_value into v;
  return v;
end;
$$;

-- IMPORTANTE: Supabase expone las funciones por la API pública. Estas dos solo deben poder
-- ejecutarse con la service_role key (el backend); si no, cualquiera con la anon key podría
-- bloquear cuentas ajenas.
revoke all on function register_failed_login(uuid, integer) from public, anon, authenticated;
revoke all on function next_sequence(text, integer, text) from public, anon, authenticated;
grant execute on function register_failed_login(uuid, integer) to service_role;
grant execute on function next_sequence(text, integer, text) to service_role;
