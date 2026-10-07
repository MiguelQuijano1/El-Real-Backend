-- Bucket PRIVADO para los documentos empresariales. El backend sube con la service_role key
-- y entrega URLs firmadas de 60 s; nadie puede listar ni leer el bucket con la anon key.
insert into storage.buckets (id, name, public, file_size_limit)
values ('documents', 'documents', false, 20971520)
on conflict (id) do nothing;
