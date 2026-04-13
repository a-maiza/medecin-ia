-- Executed automatically by PostgreSQL on first container start
-- via docker-entrypoint-initdb.d/

-- pgvector : recherche vectorielle (embeddings DrBERT / CamemBERT-bio)
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm : similarité trigramme pour la recherche full-text FR
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- uuid-ossp : génération UUID v4 côté base
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
