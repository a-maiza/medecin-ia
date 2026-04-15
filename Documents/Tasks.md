# TASKS — MédecinAI

> Dérivé de `REQUIREMENTS.md` — Tâches techniques à granularité développeur expérimenté.
> Priorités : **P0** = MVP (semaines 1-4) · **P1** = V1 (mois 2-3) · **P2** = V2 (mois 4+)

---

## 0. Infrastructure & Setup

### 0a. Environnement de développement local (à faire en premier)

- [x] **[P0]** Créer `docker-compose.dev.yml` : services `postgres` (PostgreSQL 16 + pgvector), `redis`, `rabbitmq` (avec management UI sur :15672) — volumes nommés pour persistance locale, ports exposés sur localhost uniquement.
- [x] **[P0]** Créer `.env.dev` à partir du `.env.example` avec des valeurs de développement fonctionnelles (DB locale, clés Auth0 dev tenant, clé Anthropic réelle, master key AES fictive pour tests).
- [x] **[P0]** Activer les extensions PostgreSQL dans le conteneur dev : `vector` (pgvector 0.7), `pg_trgm`, `uuid-ossp` — automatisable via script d'init Docker (`docker-entrypoint-initdb.d/`).
- [x] **[P0]** Écrire `shared/scripts/setup_db.sh` : création base, activation extensions, création rôles PostgreSQL applicatifs (`app_user`, `readonly_user`) — fonctionne contre le conteneur Docker local.
- [x] **[P0]** Créer le fichier `.env.example` avec toutes les variables listées en `REQUIREMENTS.md §8` (sans valeurs, avec commentaires indiquant la source de chaque secret).
- [x] **[P0]** Écrire `shared/scripts/health_check.sh` : vérifie connectivité PostgreSQL, Redis, RabbitMQ, GPU (nvidia-smi si dispo), endpoints FastAPI et Next.js. Doit fonctionner sans GPU (CPU fallback pour Whisper en dev).
- [x] **[P0]** Documenter dans `README.md` les étapes de démarrage local : `docker compose -f docker-compose.dev.yml up -d` → `setup_db.sh` → migrations Alembic → téléchargement modèles IA → `uvicorn` + `npm run dev`.

### 0b. CI (GitHub Actions)

- [x] **[P0]** Configurer GitHub Actions CI : lint (ruff, mypy, eslint), tests unitaires, build Docker image backend et frontend — déclenché sur PR vers `main`. Utiliser les services GitHub Actions pour PostgreSQL + Redis (pas Docker Compose en CI).

### 0c. Staging & Production (après validation dev)

- [x] **[P1]** Provisionner OVHcloud HDS : VM GPU (T4), PostgreSQL 16 managé, Redis managé, RabbitMQ — réseau privé isolé, accès SSH par clé uniquement, groupes de sécurité restrictifs.
- [x] **[P1]** Écrire `docker-compose.staging.yml` et `docker-compose.prod.yml` : images taguées, pas de volumes de dev, secrets injectés via variables d'environnement CI/CD (GitHub Secrets).
- [x] **[P1]** Configurer déploiement CD vers staging (OVHcloud) sur merge `main`, et vers prod sur tag `v*`.
- [x] **[P1]** Déployer Prometheus + Grafana on-premise HDS avec dashboard latence RAG, usage tokens LLM, taux d'erreur.
- [x] **[P1]** Configurer Loki pour ingestion des logs JSON structurés du backend (rétention 10 ans, cold storage après 2 ans).

---

## 1. Base de données & Modèle de données

- [x] **[P0]** Écrire les migrations Alembic pour toutes les entités : `Cabinet`, `Medecin`, `Patient`, `Consultation`, `Document`, `Chunk`, `DrugInteraction`, `AuditLog`, `DoctorStyleChunk`, `ValidationMetric`, `TrainingPair`, `Subscription`.
- [x] **[P0]** Ajouter les colonnes générées (`patient_id`, `doctor_id`, `specialty`, `has_grade`) sur `Chunk` avec `GENERATED ALWAYS AS ... STORED`.
- [x] **[P0]** Créer les 7 index pgvector HNSW partiels par namespace (CCAM, HAS, VIDAL, patient_history, doctor_corpus) et l'index FTS `gin(to_tsvector('french', text))`.
- [x] **[P0]** Créer l'index composite `(patient_id, doctor_id)` sur `chunks` filtré sur `source = 'patient_history'`.
- [x] **[P0]** Activer Row-Level Security sur `chunks` et `patients` : policy `SELECT` restreinte par `cabinet_id` pour le rôle applicatif. Écrire `shared/scripts/setup_rls.sql`.
- [x] **[P0]** Ajouter contrainte `CHECK (drug_a < drug_b)` et `UNIQUE (drug_a, drug_b)` sur `drug_interactions`.
- [x] **[P0]** Rendre `audit_log` append-only : trigger PostgreSQL `BEFORE UPDATE OR DELETE` qui lève une exception.
- [x] **[P0]** Écrire `shared/scripts/seed_global_kb.sh` : appelle les jobs Celery d'import CCAM, HAS mémo, BDPM interactions dans le bon ordre.

---

## 2. Authentification & Onboarding

- [x] **[P0]** Configurer tenant Auth0 : application web (Authorization Code + PKCE), API audience, règle de vérification du claim `rpps` dans l'access token.
- [x] **[P0]** Implémenter `POST /auth/register` (FastAPI) : valider format RPPS (11 chiffres), créer `Medecin` + `Cabinet` + `Subscription` (trial 14j), envoyer email de confirmation SMTP.
- [x] **[P0]** Implémenter middleware JWT FastAPI : vérifier `Authorization: Bearer` via Auth0 JWKS, injecter `current_user` (medecin_id, cabinet_id, role) dans le contexte de la requête.
- [x] **[P0]** Implémenter pages Next.js `(auth)/login` et `(auth)/register` avec Auth0 SDK (`@auth0/nextjs-auth0`).
- [x] **[P0]** Implémenter flow onboarding 3 étapes : profil médecin (spécialité, RPPS) → premier patient de test → première consultation démo.
- [x] **[P0]** Implémenter expiration de session après 8h d'inactivité (Auth0 `session.rolling: false`, `session.absoluteDuration: 28800`).
- [x] **[P1]** Intégrer Pro Santé Connect (e-CPS) comme provider OAuth2 externe Auth0 : récupérer `rpps` et `specialite` depuis le token PSC, pré-remplir le profil.
- [x] **[P1]** Implémenter refresh automatique du JWT côté frontend (intercepteur Axios/fetch, retry transparent sur 401).

---

## 3. Sécurité & Chiffrement

- [x] **[P0]** Implémenter `backend/app/security/encryption.py` : `encrypt(plaintext, patient_id) → {ciphertext_b64, nonce_b64}` et `decrypt(ciphertext_b64, nonce_b64, patient_id)` via AES-256-GCM, clé dérivée HKDF(master_key, patient_id).
- [x] **[P0]** Implémenter `backend/app/security/pseudonymizer.py` : wrapper Presidio (`AnalyzerEngine` + `AnonymizerEngine` configurés pour FR), table de correspondance token↔valeur en Redis avec TTL = durée de session.
- [x] **[P0]** Implémenter `backend/app/security/rls.py` : helpers `get_rls_context(cabinet_id, patient_id)` qui set les paramètres de session PostgreSQL pour les policies RLS.
- [x] **[P0]** Implémenter `backend/app/security/audit.py` : `log_event(event_type, doctor_rpps, patient_ins, cabinet_id, payload)` — calcule `content_hash = SHA-256(prev_hash + payload)`, insère dans `audit_log` (append-only). Exposer `verify_chain()` pour audit.
- [x] **[P0]** Configurer CORS FastAPI : `ALLOWED_ORIGINS` depuis env, TLS 1.3 enforced via reverse proxy (Nginx / Caddy), HSTS header.
- [x] **[P0]** Implémenter rate limiting Redis : middleware FastAPI qui incrémente `rate:{cabinet_id}:{minute}`, 429 après 100 req/min cabinet ou 10 req/min sur `/embed` et `/llm`.
- [x] **[P1]** Intégrer lecture clé maître depuis HSM OVHcloud (remplacer `PATIENT_ENCRYPTION_MASTER_KEY` env par appel API HSM en production).

---

## 4. Modèles IA on-premise

- [x] **[P0]** Écrire `ia/transcription/whisper_pipeline.py` : charger faster-whisper large-v3 sur GPU, exposer `transcribe_chunk(audio_pcm_16khz, initial_prompt) → {text, words: [{word, start, end, probability}], language}`. VAD webrtcvad aggressiveness=2, flush sur silence > 15 frames.
- [x] **[P0]** Écrire `ia/transcription/prompt_builder.py` : construire `initial_prompt` Whisper depuis spécialité médecin + liste médicaments actifs patient (utilisé pour améliorer la reconnaissance vocabulaire médical).
- [x] **[P0]** Écrire `ia/transcription/postprocessor.py` : normalisation abréviations (`"12 0 sur 8 0" → "120/80"`, etc.), NER médical (symptômes, médicaments, mesures) via règles + modèle spaCy `fr_core_news_lg`.
- [x] **[P0]** Charger CamemBERT-bio (`almanach/camembert-bio`) comme service embedding : `embed(texts: list[str]) → np.ndarray (N, 768)`, batch size adaptatif selon VRAM disponible.
- [x] **[P0]** Charger cross-encoder `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` : `rerank(query, passages: list[str]) → list[float]`.
- [x] **[P1]** Migrer embedding vers DrBERT-7GB-cased : remplacer CamemBERT-bio, re-indexer tous les chunks existants (job Celery avec progress tracking).

---

## 5. Pipeline RAG

- [x] **[P0]** Écrire `ia/rag/retriever/hybrid_search.py` : orchestration parallèle dense (HNSW pgvector top-20) + sparse (BM25 top-20), fusion RRF(k=60) avec poids adaptatifs (dense 0.7/sparse 0.3 par défaut, sparse 0.7 si codes CCAM/CIM détectés).
- [x] **[P0]** Écrire `ia/rag/retriever/bm25_index.py` : construire et sérialiser un index `BM25Okapi` (rank_bm25) sur tous les chunks non-patient. Exposer `search(query, top_k) → list[(chunk_id, score)]`. Rebuilder chaque nuit via job Celery.
- [x] **[P0]** Écrire `ia/rag/retriever/patient_store.py` : `PatientVectorStore` avec filtre automatique `patient_id + cabinet_id` injectés sur toute recherche — impossible d'omettre le filtre en appelant cette classe.
- [x] **[P0]** Écrire `ia/rag/retriever/query_enricher.py` : enrichir la requête brute avec spécialité médecin + DFG + grossesse + traitements actifs + allergies du patient.
- [x] **[P0]** Écrire `ia/rag/reranker/cross_encoder.py` : wrapper cross-encoder, top-12 → scores → tri.
- [x] **[P0]** Écrire `ia/rag/reranker/medical_booster.py` : appliquer facteurs cliniques (×2.0 IRC si DFG < 60, ×2.0 grossesse, ×1.8 interaction détectée, ×1.3 spécialité) sur les scores après reranking.
- [x] **[P0]** Écrire `ia/rag/reranker/mmr.py` : Maximal Marginal Relevance, lambda=0.65, top-5 finaux diversifiés.
- [x] **[P0]** Écrire `ia/soap/prompt_assembler.py` : assembler le prompt 6 couches (system → safety context → RAG chunks → style médecin NS5 → transcript → instruction finale), respecter le system prompt défini en section 6.
- [x] **[P0]** Écrire `ia/soap/output_validator.py` : valider le JSON SOAP contre le `OUTPUT_SCHEMA` (section 6), vérifier que codes CCAM/CIM-10 correspondent à des chunks fournis (regex + lookup), score < 0.70 → alerte INFO.
- [x] **[P1]** Écrire `ia/rag/indexer/ccam_indexer.py` : download ATIH CCAM, parse XML/CSV, chunking sémantique, embedding, upsert pgvector avec `source='ccam'`.
- [x] **[P1]** Écrire `ia/rag/indexer/has_indexer.py` : download fiches HAS PDF via API HAS, extraction texte pdfplumber, chunking hiérarchique (titres → sections), embedding, upsert avec métadonnées `pathologie`, `has_grade`, `annee`.
- [x] **[P1]** Écrire `ia/rag/indexer/vidal_indexer.py` : download BDPM data.gouv.fr + Thériaque interactions, normalisation DCI minuscules, upsert table `drug_interactions`, embedding notices VIDAL pour NS3.
- [x] **[P1]** Écrire `ia/rag/indexer/patient_indexer.py` : indexer le transcript + SOAP validé d'une consultation dans NS4, chiffrement AES-256-GCM du texte, embedding on-premise obligatoire.
- [x] **[P1]** Écrire `ia/rag/indexer/doctor_style_indexer.py` (NS5) : à chaque validation SOAP, calculer `quality_score` (similarité cosinus généré↔validé), créer `DoctorStyleChunk` si score > 0.7, indexer dans pgvector.
- [x] **[P1]** Écrire `ia/soap/style_learner.py` : récupérer top-3 `DoctorStyleChunk` par `(doctor_id, motif_key)` pour injection few-shot dans le prompt SOAP.

---

## 6. Service de génération SOAP

- [x] **[P0]** Implémenter `backend/app/services/soap_generator.py` : orchestrer query enrichment → RAG retrieval → interaction check → prompt assembly → appel Claude (streaming, temperature=0.15) → output validation → retour JSON SOAP.
- [x] **[P0]** Implémenter appel Claude Sonnet 4.6 en streaming : utiliser `anthropic.AsyncAnthropic`, stream section par section (S, O, A, P), envoyer les tokens via WebSocket au frontend.
- [x] **[P0]** Implémenter `backend/app/routers/soap.py` : `POST /soap/generate` (déclenche génération), `GET /soap/{consultation_id}` (retourne SOAP courant), `PATCH /soap/{consultation_id}` (sauvegarde éditions inline).
- [x] **[P0]** Implémenter `POST /soap/{consultation_id}/validate` : calculer diff généré↔validé, sauvegarder dans `Consultation.soap_validated`, créer `ValidationMetric`, déclencher indexation NS5 si score > 0.7, logger `soap_signed` dans audit_log.

---

## 7. Service de transcription (WebSocket)

- [x] **[P0]** Implémenter `backend/app/routers/transcription.py` : WebSocket endpoint `/ws/transcription/{session_id}`. Recevoir chunks audio PCM (base64), appeler `WhisperPipeline.transcribe_chunk`, retourner `{text, words, is_final}` en streaming.
- [x] **[P0]** Implémenter auto-save chiffré toutes les 30s : à chaque flush Whisper, chiffrer le transcript partiel et `UPDATE Consultation SET transcript_encrypted = ...`.
- [x] **[P0]** Implémenter `backend/app/services/transcription.py` : gestion état session (début, chunks reçus, fin), coordination VAD + Whisper + postprocessor.

---

## 8. Alertes cliniques

- [x] **[P0]** Implémenter `backend/app/services/interaction_checker.py` : normaliser DCI (table de 200+ entrées nom commercial → DCI minuscules), requête SQL `WHERE drug_a IN (...) AND drug_b IN (...)` sur `drug_interactions`, retourner liste triée par sévérité décroissante. Doit s'exécuter en < 5ms.
- [x] **[P0]** Implémenter logique de blocage SOAP : si `severity = 'CI_ABSOLUE'` → `soap = null`, `alerts` contient alerte CRITIQUE. Si `CI_RELATIVE` → idem, mais déblocable après justification. Si `PRECAUTION` → SOAP généré avec mention bloc P.
- [x] **[P0]** Implémenter alerte dosage DFG : lors de l'assemblage du prompt SOAP, vérifier via RAG NS3 si les médicaments prescrits ont une restriction rénale ; si DFG patient < seuil VIDAL → alerte ATTENTION ou CRITIQUE selon seuil.
- [x] **[P0]** Implémenter `backend/app/routers/interactions.py` : `POST /interactions/check` acceptant `{new_drugs, active_drugs}`, retournant la liste des interactions (utilisé par le frontend pour feedback temps réel pendant la saisie).

---

## 9. Base de connaissances

- [x] **[P0]** Implémenter `backend/app/routers/documents.py` : `POST /documents/upload` (multipart, max 50 Mo, PDF/DOCX uniquement), `GET /documents` (liste filtrée par `cabinet_id` ou globale pour admin), `DELETE /documents/{id}` (soft delete `deprecated=true` + exclusion chunks).
- [x] **[P0]** Implémenter job Celery `backend/app/jobs/index_document.py` : extraction texte (pdfplumber pour PDF, python-docx pour DOCX), chunking sémantique (512 tokens, overlap 64), embedding CamemBERT-bio, upsert pgvector. Exposer progression via Redis pub/sub.
- [x] **[P0]** Implémenter jobs Celery périodiques : `sync_ccam.py` (hebdomadaire ATIH), `sync_has.py` (mensuelle HAS), `sync_vidal.py` (quotidienne BDPM + Thériaque). Détection delta via `content_hash` SHA-256.
- [x] **[P0]** Implémenter `backend/app/routers/rag.py` : `POST /rag/query` — enrichir requête, chercher dans les 5 namespaces (NS4 uniquement si `patient_id` fourni), reranker, répondre via Claude avec prompt RAG (section 6). Retourner `{answer, sources: [{namespace, document_title, section}]}`.

---

## 10. Dossiers patients

- [x] **[P0]** Implémenter `backend/app/services/patient_service.py` + `backend/app/routers/patients.py` : CRUD patient avec chiffrement AES-256-GCM sur `nom`, `allergies`, `traitements_actifs`, `antecedents`. Cache Redis sur `patient_id` (TTL 5min, jamais de cache NS4). Recherche par `nom` (déchiffrement à la volée) ou `ins`.
- [x] **[P0]** Implémenter `backend/app/routers/consultations.py` : `POST /consultations` (créer), `GET /consultations/{id}` (retourner avec SOAP déchiffré), `GET /patients/{id}/consultations` (historique filtré cabinet).

---

## 11. Export & Interopérabilité

- [x] **[P1]** Implémenter `backend/app/services/export_service.py` — conversion SOAP → ressource FHIR R4 `Composition` (Python `fhir.resources`). Valider contre le profil FHIR FR.
- [x] **[P1]** Implémenter export DMP : `POST /export/dmp/{consultation_id}` — requérir signature e-CPS active dans le contexte Auth, pousser vers MSSanté gateway, stocker `dmp_document_id` dans la consultation, logger `dmp_exported`.
- [x] **[P1]** Implémenter sync Doctolib : `POST /export/doctolib/{consultation_id}` — vérifier token Doctolib configuré pour le médecin, appel API Doctolib en parallèle de l'export DMP (`asyncio.gather`), notification UI si erreur.
- [x] **[P1]** Implémenter génération PDF signé : `GET /export/pdf/{consultation_id}` — utiliser `reportlab` ou `weasyprint`, inclure 4 sections SOAP, nom/RPPS médecin, date, logo cabinet. Retourner PDF en streaming.
- [x] **[P1]** Implémenter `backend/app/routers/export.py` orchestrant les 3 canaux avec gestion d'erreur isolée (échec DMP ne bloque pas PDF).

---

## 12. Abonnement & Facturation

- [x] **[P1]** Configurer produits Stripe : 3 plans (Solo ~150€, Cabinet, Réseau) avec `Price` récurrents mensuels. Stocker les `Price ID` dans les env vars.
- [x] **[P1]** Implémenter `POST /billing/checkout` : créer session Stripe Checkout, rediriger vers page paiement. Implémenter `POST /billing/portal` : portail Stripe Customer Portal pour gestion/annulation.
- [x] **[P1]** Implémenter webhook Stripe `POST /webhooks/stripe` : vérifier signature (`stripe.Webhook.construct_event`), gérer `invoice.paid`, `customer.subscription.updated`, `customer.subscription.deleted` — mettre à jour `Subscription.status` en base.
- [x] **[P1]** Implémenter garde d'accès : middleware FastAPI vérifiant que le cabinet a un abonnement actif ou trial valide (`trial_ends_at > now()`). Lecture seule si expiré (pas de nouvelle consultation).

---

## 13. Interface Frontend

### Authentification & Onboarding
- [x] **[P0]** Pages `(auth)/login` et `(auth)/register` avec Auth0 provider. Afficher CGU/politique de confidentialité à l'inscription (case à cocher obligatoire).
- [x] **[P0]** Flow onboarding `(auth)/onboarding` : 3 étapes avec stepper shadcn/ui — saisie profil médecin, ajout premier patient de test, lancement consultation démo.

### Consultation
- [x] **[P0]** Page `(dashboard)/consultation/new` : sélection patient, bouton "Démarrer l'enregistrement", visualisation transcription live (tokens WebSocket avec surlignage couleur : orange si probabilité < 70%, rouge si < 50%).
- [x] **[P0]** Composant `consultation/TranscriptViewer` : affichage streaming des mots avec couleur par niveau de confiance, indicateur d'enregistrement.
- [x] **[P0]** Composant `consultation/SOAPEditor` : affichage section par section (S, O, A, P) en streaming, champs éditables inline, badges alertes avec modalité d'acquittement.
- [x] **[P0]** Logique de blocage UI : bouton "Valider et signer" désactivé si alerte CRITIQUE non acquittée. Champ "justification clinique" affiché obligatoire pour CI_RELATIVE.
- [x] **[P0]** Auto-save visuel toutes les 30s avec indicateur de sauvegarde.

### RAG / Chat
- [x] **[P0]** Composant `rag/ClinicalChat` : input question naturelle, affichage réponse Claude en streaming, citations sources (namespace + titre + section) avec lien cliquable vers le document.

### Base de connaissances
- [x] **[P0]** Page `(dashboard)/knowledge-base` : liste des documents privés cabinet avec statut d'indexation, bouton upload (PDF/DOCX, 50 Mo max), suppression.
- [x] **[P0]** Indicateur de progression Celery : polling `GET /documents/{id}/status` toutes les 2s pendant indexation, barre de progression shadcn.
- [x] **[P1]** Page `(admin)/knowledge-base` : upload documents globaux (HAS, CCAM, VIDAL), liste avec filtre par source, soft delete, métriques de la base (nb chunks par namespace).

### Patients
- [x] **[P0]** Page `(dashboard)/patients` : liste patients cabinet, recherche par nom/INS, création dossier patient.
- [x] **[P0]** Page `(dashboard)/patients/[id]` : fiche patient avec données déchiffrées (nom, allergies, traitements, antécédents), mise à jour DFG, liste consultations passées.

### Paramètres
- [x] **[P0]** Page `(dashboard)/settings` : profil médecin, spécialité, configuration token Doctolib (champ masqué).
- [x] **[P1]** Section abonnement : plan actuel, date fin trial/renouvellement, bouton upgrade (Stripe Checkout), bouton gérer (Stripe Portal).

### Admin
- [x] **[P1]** Page `(admin)/dashboard` : liste cabinets + statut abonnement, métriques RAG (score moyen par namespace, p50/p95 latence), accessible uniquement `role='admin_medecinai'`.

---

## 14. Tests

- [x] **[P0]** Tests unitaires `backend/tests/` : `test_encryption.py` (round-trip AES, dérivation HKDF), `test_audit.py` (chaînage hashes, append-only), `test_interaction_checker.py` (normalisation DCI, sévérités), `test_soap_validator.py` (JSON schema, codes CCAM/CIM invalides rejetés).
- [x] **[P0]** Tests intégration : `test_rag_pipeline.py` avec base de test PostgreSQL + fixtures chunks — vérifier isolation patient, seuil score 0.65, format sources retournées.
- [x] **[P0]** Tests WebSocket transcription : mock faster-whisper, vérifier format messages streaming, auto-save toutes les 30s.
- [ ] **[P1]** Tests end-to-end (Playwright) : flow inscription → onboarding → création consultation → transcription → SOAP → validation → export PDF.
- [ ] **[P1]** Tests de charge (Locust) : 50 connexions WebSocket simultanées, vérifier latence transcription < 1s perçue ; 100 req/min endpoint RAG, vérifier rate limiting 429 au-delà.
