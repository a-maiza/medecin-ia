-- Rôles PostgreSQL applicatifs
-- app_user  : rôle de l'application FastAPI (DML + SELECT, soumis aux RLS policies)
-- readonly_user : rôle lecture seule pour monitoring / analytics

-- app_user est déjà le POSTGRES_USER créé par l'image Docker ;
-- on lui accorde les droits sur la base puis on crée readonly_user.

GRANT ALL PRIVILEGES ON DATABASE medecinai TO app_user;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly_user') THEN
    CREATE ROLE readonly_user WITH LOGIN PASSWORD 'dev_readonly_password';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE medecinai TO readonly_user;

-- Les droits sur les tables seront accordés après les migrations Alembic
-- via setup_db.sh (GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user).
