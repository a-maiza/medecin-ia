# REQUIREMENTS — MédecinAI

> Généré le 12 avril 2026 à partir du brainstorming produit — Source de vérité pour Claude Code.
> Ce document est auto-suffisant. Claude Code ne doit consulter aucune source externe.

---

## 1. Vue d'ensemble

**Nom du projet :** MédecinAI Support  
**Catégorie :** B2B SaaS médical  
**Tagline :** Redonner 1h30 par jour à chaque médecin généraliste en automatisant la paperasse administrative.

### Description courte
MédecinAI est une application web SaaS destinée aux médecins libéraux. Elle transcrit les consultations en temps réel, génère automatiquement des comptes-rendus structurés au format SOAP, détecte les interactions médicamenteuses, et répond aux questions cliniques via un moteur RAG médical. L'IA est au cœur du produit.

### Objectif métier
Réduire le temps administratif des médecins libéraux de 1h30/jour minimum, en automatisant : transcription audio → compte-rendu SOAP → codification CCAM/CIM-10 → export DMP/Doctolib.

### Public cible
- **Principal :** Médecins généralistes libéraux français (100 000 cibles en France).
- **Secondaire :** Spécialistes libéraux (cardiologues, pneumologues, psychiatres, kinésithérapeutes, infirmiers libéraux).
- **Géographies initiales :** France (marché principal), Algérie (marché secondaire — loi 18-07).

### Langue
Français uniquement. Aucun support multilingue prévu à ce stade.

### Modèle de revenus
- **Modèle :** SaaS par abonnement mensuel.
- **Tarification indicative :** ~150 €/médecin/mois (3 tiers : Solo / Cabinet / Réseau).
- **Trial :** 14 jours gratuits sans carte bancaire.
- **Paiement France :** Stripe (TVA 20%).
- **Paiement Algérie :** [À PRÉCISER — Stripe non disponible, virement bancaire manuel pour le MVP].
- **Objectif J90 :** 50 abonnés payants, MRR ~7 500 €.

---

## 2. Stack technique

| Couche | Technologie | Justification |
|---|---|---|
| Frontend | Next.js 14 (App Router) + TypeScript | SSR natif, routing intégré, écosystème React mature, déploiement Vercel simple pour équipe solo. |
| UI Components | Tailwind CSS + shadcn/ui | Composants accessibles prêts à l'emploi, customisables, cohérents avec le design system. |
| Backend | FastAPI (Python 3.12) | Async natif, typage fort via Pydantic, idéal pour pipelines ML/IA, excellent support asyncpg. |
| Base de données | PostgreSQL 16 + pgvector 0.7 | Base relationnelle robuste + recherche vectorielle native, certifiable HDS, déployable OVHcloud. |
| Cache | Redis 7 | Sessions, rate-limiting, cache résultats RAG (TTL adaptatif), queue Celery. |
| File d'attente | Celery + RabbitMQ | Jobs asynchrones : transcription audio, indexation documents, sync DMP. |
| Auth | Auth0 + CPS card (e-CPS / Pro Santé Connect) | OAuth2 standard pour l'accès web + identité numérique de santé réglementaire française. |
| ASR (transcription) | Whisper large-v3 (faster-whisper, on-premise GPU) | Meilleure précision sur vocabulaire médical FR avec initial_prompt contextuel, déployable HDS. |
| LLM | Claude claude-sonnet-4-6 (Anthropic API) | Génération SOAP, alertes cliniques, réponses RAG ; temperature=0.15 pour précision médicale. |
| Embedding | DrBERT-7GB-cased (on-premise) | Modèle médical FR pré-entraîné sur corpus CCAM/PubMed FR, 89% précision@5 vs 71% pour OpenAI sur vocabulaire spécialisé. |
| Embedding fallback | CamemBERT-bio (MVP) | Plus léger (768d), déployable rapidement en attendant DrBERT sur GPU dédié. |
| Reranker | cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 | Cross-encoder multilingue, ~5ms sur 20 paires GPU, +25% précision vs retrieval seul. |
| Hébergement backend | OVHcloud HDS (certifié Hébergement Données de Santé) | Certification HDS obligatoire pour données patients France, DC France, RGPD-ready. |
| Hébergement frontend | Vercel (Pro) | CDN global, déploiements atomiques, preview par branche. |
| PDF parsing | pdfplumber + pypdf2 | Extraction texte avec préservation hiérarchie titres pour chunking sémantique HAS/VIDAL. |
| PII detection | Presidio (Microsoft, open source) | Détection et pseudonymisation PII avant envoi API externe, support FR. |
| Monitoring | Prometheus + Grafana | Métriques latence RAG, usage LLM, taux erreur, déployables on-premise HDS. |
| Logs | Structured JSON → Loki | Audit trail immuable requis HDS, rétention 10 ans. |
| CI/CD | GitHub Actions | Tests automatiques, déploiement staging/prod sur merge main. |

---

## 3. Architecture & structure du projet

```
/medecinai/
├── frontend/                          # Next.js 14 App Router
│   ├── app/
│   │   ├── (auth)/                    # Pages login, onboarding
│   │   ├── (dashboard)/               # Interface médecin connecté
│   │   │   ├── consultation/          # Transcription live + SOAP
│   │   │   ├── knowledge-base/        # Gestion documents cabinet
│   │   │   ├── patients/              # Dossiers patients
│   │   │   └── settings/              # Paramètres compte
│   │   ├── (admin)/                   # Interface admin MédecinAI
│   │   │   └── knowledge-base/        # Gestion base globale
│   │   └── api/                       # Route handlers Next.js (proxy auth)
│   ├── components/
│   │   ├── ui/                        # shadcn/ui components
│   │   ├── consultation/              # Composants transcription + SOAP
│   │   ├── rag/                       # Composants recherche + sources
│   │   └── shared/                    # Header, Nav, AlertBanner
│   └── lib/
│       ├── auth/                      # Auth0 client
│       ├── api/                       # Clients API backend
│       └── utils/
│
├── backend/                           # FastAPI Python
│   ├── app/
│   │   ├── main.py                    # Point d'entrée FastAPI
│   │   ├── routers/
│   │   │   ├── consultations.py       # Endpoints consultation
│   │   │   ├── transcription.py       # WebSocket transcription live
│   │   │   ├── soap.py                # Génération SOAP
│   │   │   ├── rag.py                 # Questions RAG
│   │   │   ├── documents.py           # Upload/gestion documents
│   │   │   ├── patients.py            # Dossiers patients
│   │   │   ├── interactions.py        # Vérification interactions médicamenteuses
│   │   │   ├── export.py              # Export DMP / Doctolib / PDF
│   │   │   └── admin.py               # Routes admin
│   │   ├── models/                    # Modèles SQLAlchemy
│   │   ├── schemas/                   # Schémas Pydantic (input/output)
│   │   ├── services/
│   │   │   ├── transcription.py       # Pipeline Whisper + VAD
│   │   │   ├── soap_generator.py      # Assemblage prompt + appel Claude
│   │   │   ├── interaction_checker.py # Lookup déterministe + RAG explicatif
│   │   │   ├── export_service.py      # FHIR R4, PDF signé, Doctolib sync
│   │   │   └── patient_service.py     # CRUD patients + cache Redis
│   │   ├── security/
│   │   │   ├── encryption.py          # AES-256-GCM chiffrement chunks patient
│   │   │   ├── pseudonymizer.py       # Presidio PII detection + pseudonymisation
│   │   │   ├── rls.py                 # Helpers Row-Level Security PostgreSQL
│   │   │   └── audit.py               # Audit trail immuable (hash chaîné)
│   │   └── jobs/                      # Celery tasks
│   │       ├── sync_ccam.py           # Sync hebdomadaire ATIH
│   │       ├── sync_has.py            # Sync mensuelle HAS
│   │       ├── sync_vidal.py          # Sync quotidienne BDPM
│   │       └── index_document.py      # Indexation async documents uploadés
│   ├── migrations/                    # Alembic
│   └── tests/
│
├── ia/                                # Modules IA découplés
│   ├── rag/
│   │   ├── indexer/
│   │   │   ├── ccam_indexer.py        # Pipeline indexation CCAM (NS1)
│   │   │   ├── has_indexer.py         # Pipeline indexation HAS (NS2)
│   │   │   ├── vidal_indexer.py       # Pipeline indexation VIDAL/interactions (NS3)
│   │   │   ├── patient_indexer.py     # Pipeline indexation historique patient (NS4)
│   │   │   └── doctor_style_indexer.py# Pipeline indexation corpus médecin (NS5)
│   │   ├── retriever/
│   │   │   ├── hybrid_search.py       # Recherche hybride dense+BM25+RRF
│   │   │   ├── query_enricher.py      # Enrichissement requête avec profil patient
│   │   │   ├── bm25_index.py          # Index BM25Okapi en mémoire
│   │   │   └── patient_store.py       # PatientVectorStore isolé
│   │   └── reranker/
│   │       ├── cross_encoder.py       # Cross-encoder + score médical
│   │       ├── medical_booster.py     # Boosting clinique (IRC, grossesse, etc.)
│   │       └── mmr.py                 # Maximal Marginal Relevance déduplication
│   ├── transcription/
│   │   ├── whisper_pipeline.py        # Faster-Whisper + VAD + streaming
│   │   ├── prompt_builder.py          # Initial prompt contextuel par spécialité
│   │   └── postprocessor.py           # Normalisation + NER entités cliniques
│   ├── soap/
│   │   ├── prompt_assembler.py        # Assemblage 6 couches prompt SOAP
│   │   ├── output_validator.py        # Validation codes CCAM/CIM-10 post-génération
│   │   └── style_learner.py           # NS5 : capture + indexation style médecin
│   └── prompts/
│       ├── soap_system.py             # System prompt SOAP
│       ├── rag_system.py              # System prompt RAG
│       └── specialty_vocab.py         # Vocabulaire par spécialité (Whisper initial_prompt)
│
├── knowledge-base/
│   ├── global/                        # Scripts d'import base globale
│   │   ├── ccam/                      # Fichiers ATIH CCAM
│   │   ├── has/                       # PDFs recommandations HAS
│   │   ├── vidal/                     # Données BDPM + interactions
│   │   └── cim10/                     # Codes CIM-10 FR
│   └── private/                       # Répertoire uploads médecins (isolé par cabinet_id)
│
└── shared/
    ├── types/                         # Types TypeScript partagés frontend/backend
    ├── constants/                     # Constantes partagées
    └── scripts/
        ├── setup_db.sh                # Création tables + extensions + RLS
        ├── seed_global_kb.sh          # Import initial base globale
        └── health_check.sh            # Vérification stack complète
```

---

## 4. Modèle de données

### Entité : Cabinet
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| nom | varchar(200) | NOT NULL |
| adresse | text | NOT NULL |
| pays | enum | 'FR' \| 'DZ', NOT NULL |
| siret | varchar(14) | Nullable (FR uniquement) |
| rpps_titulaire | varchar(11) | FK → Medecin.rpps, NOT NULL |
| stripe_customer_id | varchar(100) | Nullable |
| plan | enum | 'trial' \| 'solo' \| 'cabinet' \| 'reseau', DEFAULT 'trial' |
| trial_ends_at | timestamptz | Nullable |
| created_at | timestamptz | DEFAULT NOW() |
| updated_at | timestamptz | AUTO |

### Entité : Medecin
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| cabinet_id | uuid | FK → Cabinet, NOT NULL |
| rpps | varchar(11) | UNIQUE, NOT NULL |
| email | varchar(254) | UNIQUE, NOT NULL |
| nom | varchar(100) | NOT NULL |
| prenom | varchar(100) | NOT NULL |
| specialite | varchar(100) | NOT NULL |
| auth0_sub | varchar(100) | UNIQUE, NOT NULL |
| role | enum | 'medecin' \| 'admin_cabinet' \| 'admin_medecinai', NOT NULL |
| preferences | jsonb | DEFAULT '{}' |
| created_at | timestamptz | DEFAULT NOW() |

### Entité : Patient
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| cabinet_id | uuid | FK → Cabinet, NOT NULL |
| ins | varchar(22) | Identité Nationale de Santé, UNIQUE par cabinet |
| nom_pseudonyme | text | Chiffré AES-256-GCM, NOT NULL |
| date_naissance_hash | varchar(64) | SHA-256 date naissance, NOT NULL |
| sexe | enum | 'M' \| 'F' \| 'autre' |
| allergies_encrypted | text | Chiffré AES-256-GCM |
| traitements_actifs_encrypted | text | Chiffré AES-256-GCM |
| antecedents_encrypted | text | Chiffré AES-256-GCM |
| dfg | float | Nullable, DFG en mL/min/1.73m² |
| grossesse | boolean | DEFAULT false |
| doctolib_patient_id | varchar(100) | Nullable |
| created_at | timestamptz | DEFAULT NOW() |
| updated_at | timestamptz | AUTO |

### Entité : Consultation
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| cabinet_id | uuid | FK → Cabinet, NOT NULL |
| medecin_id | uuid | FK → Medecin, NOT NULL |
| patient_id | uuid | FK → Patient, NOT NULL |
| date | timestamptz | NOT NULL |
| motif | text | NOT NULL |
| transcript_encrypted | text | Chiffré AES-256-GCM |
| soap_generated | jsonb | SOAP tel que généré par le LLM |
| soap_validated | jsonb | SOAP après correction médecin |
| quality_score | float | Score similarité généré/validé (0-1) |
| correction_types | text[] | Types de corrections effectuées |
| status | enum | 'in_progress' \| 'generated' \| 'validated' \| 'exported' |
| alerts | jsonb | Alertes cliniques générées |
| chunks_used | text[] | IDs des chunks RAG utilisés |
| dmp_document_id | varchar(100) | Nullable, après export DMP |
| doctolib_consultation_id | varchar(100) | Nullable |
| created_at | timestamptz | DEFAULT NOW() |
| updated_at | timestamptz | AUTO |

### Entité : Document
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| cabinet_id | uuid | FK → Cabinet, null si document global |
| type | enum | 'global' \| 'private', NOT NULL |
| source | enum | 'ccam' \| 'has' \| 'vidal' \| 'cim10' \| 'upload_medecin', NOT NULL |
| filename | varchar(500) | NOT NULL |
| content_hash | varchar(64) | SHA-256 du contenu, pour delta sync |
| content_raw | text | Contenu texte extrait (non chiffré pour base globale) |
| pathologie | varchar(200) | Nullable, pour documents HAS |
| specialite | varchar(100) | Nullable |
| annee | varchar(4) | Nullable |
| url_source | text | Nullable, URL d'origine |
| deprecated | boolean | DEFAULT false, soft delete |
| uploaded_by | uuid | FK → Medecin, nullable si global |
| uploaded_at | timestamptz | DEFAULT NOW() |

### Entité : Chunk
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| document_id | uuid | FK → Document, NOT NULL |
| source | text | 'ccam' \| 'has' \| 'vidal' \| 'patient_history' \| 'doctor_corpus' \| 'vidal_interactions', NOT NULL |
| text | text | Contenu du chunk (chiffré si source='patient_history') |
| encrypted_text | jsonb | Nullable, {ciphertext, nonce} si patient |
| embedding | vector(768) | pgvector, DrBERT 768 dims, NOT NULL |
| metadata | jsonb | NOT NULL DEFAULT '{}' |
| patient_id | text | GENERATED ALWAYS AS (metadata->>'patient_id') STORED |
| doctor_id | text | GENERATED ALWAYS AS (metadata->>'doctor_id') STORED |
| specialty | text | GENERATED ALWAYS AS (metadata->>'specialite') STORED |
| has_grade | text | GENERATED ALWAYS AS (metadata->>'has_grade') STORED |
| position | int | Ordre dans le document |
| updated_at | timestamptz | DEFAULT NOW() |

### Entité : DrugInteraction
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| drug_a | text | DCI normalisée minuscules, NOT NULL |
| drug_b | text | DCI normalisée minuscules, NOT NULL |
| severity | enum | 'CI_ABSOLUE' \| 'CI_RELATIVE' \| 'PRECAUTION' \| 'SURVEILLANCE' \| 'INFO', NOT NULL |
| mechanism | text | Nullable |
| consequence | text | Nullable |
| management | text | Nullable |
| alternative | text | Nullable |
| source | text | 'vidal' \| 'theriaque' \| 'ansm', NOT NULL |
| source_version | text | NOT NULL |
| updated_at | timestamptz | DEFAULT NOW() |
| CONSTRAINT | | drug_a < drug_b (paire canonique alphabétique) |
| UNIQUE | | (drug_a, drug_b) |

### Entité : AuditLog
| Champ | Type | Contraintes |
|---|---|---|
| id | bigserial | PK |
| event_type | text | NOT NULL |
| doctor_rpps | varchar(11) | Nullable |
| patient_ins | varchar(22) | Nullable |
| cabinet_id | uuid | Nullable |
| content_hash | varchar(64) | Hash SHA-256 de cette entrée |
| prev_hash | varchar(64) | Hash de l'entrée précédente (chaînage) |
| payload | jsonb | Détails de l'événement |
| created_at | timestamptz | DEFAULT NOW(), NOT NULL |
| NOTE | | Table append-only — aucun UPDATE ni DELETE autorisé |

### Entité : DoctorStyleChunk
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| doctor_id | uuid | FK → Medecin, NOT NULL |
| motif_key | text | Motif canonique normalisé, NOT NULL |
| motif_raw | text | Motif tel que saisi, NOT NULL |
| text | text | SOAP exemple complet (few-shot), NOT NULL |
| embedding | vector(768) | pgvector, NOT NULL |
| quality_score | float | Score similarité validé (0-1), NOT NULL |
| created_at | timestamptz | DEFAULT NOW() |
| INDEX | | (doctor_id, motif_key, created_at) |

### Entité : ValidationMetric
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| doctor_id | uuid | FK → Medecin, NOT NULL |
| consultation_id | uuid | FK → Consultation, NOT NULL |
| motif | text | NOT NULL |
| global_score | float | NOT NULL |
| section_scores | jsonb | {S, O, A, P} scores |
| correction_types | text[] | |
| created_at | timestamptz | DEFAULT NOW() |

### Entité : TrainingPair
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| doctor_specialty | text | NOT NULL (jamais identité) |
| generated_soap | jsonb | SOAP généré pseudonymisé |
| validated_soap | jsonb | SOAP validé pseudonymisé |
| quality_score | float | NOT NULL |
| correction_types | text[] | |
| created_at | timestamptz | DEFAULT NOW() |

### Entité : Subscription
| Champ | Type | Contraintes |
|---|---|---|
| id | uuid | PK, auto-generated |
| cabinet_id | uuid | FK → Cabinet, UNIQUE, NOT NULL |
| plan | enum | 'solo' \| 'cabinet' \| 'reseau', NOT NULL |
| status | enum | 'active' \| 'past_due' \| 'cancelled' \| 'trialing', NOT NULL |
| stripe_subscription_id | varchar(100) | Nullable (FR uniquement) |
| monthly_price_eur | decimal(10,2) | NOT NULL |
| billing_cycle_start | date | NOT NULL |
| current_period_end | date | NOT NULL |
| created_at | timestamptz | DEFAULT NOW() |

---

## 5. Fonctionnalités

### Module : Authentification & Onboarding

#### Inscription médecin — P0
- **Description :** Création de compte via Auth0 avec vérification RPPS et création du cabinet associé.
- **Critères d'acceptance :**
  - [ ] Un médecin peut s'inscrire avec email + mot de passe.
  - [ ] Le champ RPPS est validé (format 11 chiffres, vérification format).
  - [ ] Un cabinet est créé automatiquement à l'inscription avec le médecin comme admin_cabinet.
  - [ ] Un email de confirmation est envoyé avant activation du compte.
  - [ ] Le trial de 14 jours démarre automatiquement à la première connexion.
  - [ ] L'onboarding guide le médecin en 3 étapes max (profil, spécialité, premier essai).

#### Connexion sécurisée — P0
- **Description :** Authentification via Auth0 avec support e-CPS.
- **Critères d'acceptance :**
  - [ ] Connexion email/mot de passe fonctionnelle.
  - [ ] Support Pro Santé Connect (OAuth2 e-CPS) pour signature réglementaire.
  - [ ] Session expirée après 8h d'inactivité.
  - [ ] Token JWT rafraîchi automatiquement sans interruption de session active.

---

### Module : Transcription temps réel

#### Transcription audio live — P0
- **Description :** Capture audio WebRTC depuis le navigateur, transcription Whisper en streaming, affichage temps réel.
- **Critères d'acceptance :**
  - [ ] L'application peut démarrer une session d'enregistrement depuis Chrome/Firefox/Safari.
  - [ ] Le texte transcrit apparaît dans l'interface avec une latence perçue < 1s (affichage partiel en streaming).
  - [ ] Le modèle Whisper est initialisé avec l'initial_prompt contextuel (spécialité médecin + traitements actifs patient).
  - [ ] Les mots avec confiance < 70% sont surlignés visuellement dans l'interface (orange).
  - [ ] Les mots avec confiance < 50% sont affichés en rouge et marqués "à vérifier".
  - [ ] Le transcript est sauvegardé chiffré toutes les 30s (auto-save).
  - [ ] L'enregistrement fonctionne sur connexion 4G avec latence 200ms.

#### Normalisation post-transcription — P0
- **Description :** Correction du vocabulaire médical et extraction des entités cliniques.
- **Critères d'acceptance :**
  - [ ] Les abréviations médicales courantes sont normalisées (ex: "TA" → "TA", "IMC" → "IMC", "12 0 sur 8 0" → "120/80").
  - [ ] Le NER extrait : symptômes, médicaments cités, mesures cliniques.
  - [ ] Le transcript final contient les entités extraites comme champs structurés séparés.

---

### Module : Génération SOAP

#### Génération compte-rendu SOAP — P0
- **Description :** Assemblage du prompt complet (6 couches) et appel Claude pour produire un SOAP JSON structuré.
- **Critères d'acceptance :**
  - [ ] Le SOAP est généré en < 3s après la fin de consultation (latence totale end-to-end).
  - [ ] La sortie est un JSON valide correspondant au schéma OUTPUT_SCHEMA défini (section 6).
  - [ ] Si une allergie est détectée dans la prescription, le champ `soap` est `null` et `alerts` contient une alerte `CI_ABSOLUE`.
  - [ ] Chaque section SOAP ne contient que des informations présentes dans le transcript (`[non mentionné]` si absent).
  - [ ] Les codes CCAM et CIM-10 utilisés sont exclusivement issus des chunks RAG (pas générés de mémoire).
  - [ ] Le SOAP s'affiche en streaming section par section (S, O, A, P successivement).

#### Validation inline et correction — P0
- **Description :** Interface d'édition du SOAP avant validation, avec verrous sur les alertes critiques.
- **Critères d'acceptance :**
  - [ ] Chaque champ du SOAP est éditable directement dans l'interface.
  - [ ] Le bouton "Valider et signer" est désactivé tant qu'une alerte CRITIQUE n'est pas explicitement acquittée.
  - [ ] Une alerte CI_RELATIVE affiche un champ texte obligatoire "justification clinique" avant déblocage.
  - [ ] Les modifications sont trackées (diff généré ↔ validé) et stockées pour le flywheel NS5.
  - [ ] La validation déclenche la signature e-CPS.

---

### Module : Alertes cliniques

#### Détection interactions médicamenteuses — P0
- **Description :** Vérification exhaustive par lookup SQL déterministe de toutes les paires médicament × traitement actif avant génération SOAP.
- **Critères d'acceptance :**
  - [ ] Toutes les paires (nouvelles prescriptions × traitements actifs) sont vérifiées en < 5ms.
  - [ ] Une CI_ABSOLUE bloque la génération SOAP et affiche l'alerte immédiatement.
  - [ ] Une CI_RELATIVE bloque aussi, avec option de déblocage après justification.
  - [ ] Une PRECAUTION génère le SOAP avec mention dans le bloc P et alerte ATTENTION.
  - [ ] La normalisation DCI fonctionne pour les noms commerciaux courants (Crestor → rosuvastatine, Tahor → atorvastatine, etc. — liste minimale de 200 entrées).

#### Alerte dosage selon fonction rénale — P0
- **Description :** Détection automatique des surdosages si DFG patient < seuils VIDAL.
- **Critères d'acceptance :**
  - [ ] Si DFG patient < 60 mL/min et prescription d'un médicament à élimination rénale, une alerte ATTENTION est générée.
  - [ ] Si DFG < 30 mL/min et CI absolue médicament, alerte CRITIQUE générée.
  - [ ] L'alerte cite le seuil exact et la source VIDAL/HAS utilisée.

---

### Module : RAG & Questions cliniques

#### Chat médical RAG — P0
- **Description :** Interface de questions-réponses médicales utilisant le pipeline RAG complet (5 namespaces).
- **Critères d'acceptance :**
  - [ ] Une question en langage naturel retourne une réponse en < 10s end-to-end.
  - [ ] La réponse cite explicitement les sources utilisées (namespace + titre document + section).
  - [ ] Les chunks retournés du NS4 appartiennent uniquement au patient_id + cabinet_id de la session active.
  - [ ] Si aucun chunk avec score > 0.65 n'est trouvé, la réponse indique explicitement "Aucun document pertinent trouvé dans votre base de connaissances".
  - [ ] Les alertes cliniques (allergies, interactions) sont toujours injectées directement, jamais uniquement via RAG.

---

### Module : Base de connaissances

#### Gestion de la base globale — P0
- **Description :** Interface admin MédecinAI pour uploader et indexer les documents médicaux de référence (HAS, Vidal, CCAM, CIM-10) dans la base vectorielle partagée.
- **Critères d'acceptance :**
  - [ ] Un admin (role='admin_medecinai') peut uploader un PDF ou DOCX depuis l'interface.
  - [ ] Le document est chunké automatiquement (splitter sémantique médical) et ses embeddings DrBERT stockés dans pgvector avec cabinet_id = null.
  - [ ] Le document apparaît dans la liste de la base globale après indexation complète.
  - [ ] Un admin peut supprimer un document (soft delete : deprecated=true) et ses chunks associés sont exclus des recherches.
  - [ ] L'indexation est asynchrone (Celery) avec indicateur de progression dans l'interface.

#### Gestion de la base privée par cabinet — P0
- **Description :** Interface médecin pour uploader ses propres documents (protocoles internes, ordonnances-types, notes) indexés sous son cabinet_id.
- **Critères d'acceptance :**
  - [ ] Un médecin connecté peut uploader un PDF ou DOCX (max 50 Mo par fichier).
  - [ ] Les chunks sont stockés avec le cabinet_id du médecin connecté et source='upload_medecin'.
  - [ ] Un médecin ne peut jamais voir, lister, ni rechercher dans les documents d'un autre cabinet (RLS PostgreSQL + filtre applicatif).
  - [ ] Le médecin peut supprimer ses propres documents (suppression physique chunks + document).
  - [ ] Formats acceptés : PDF, DOCX. Autres formats → message d'erreur explicite.

---

### Module : Export & Interopérabilité

#### Export DMP via MSSanté — P1
- **Description :** Push du SOAP validé vers le Dossier Médical Partagé en format FHIR R4.
- **Critères d'acceptance :**
  - [ ] Le SOAP validé est converti en ressource FHIR R4 Composition valide.
  - [ ] L'export nécessite une signature e-CPS active.
  - [ ] Le push MSSanté retourne un document_id DMP stocké dans la consultation.
  - [ ] L'export est possible uniquement après validation + signature médecin.
  - [ ] En cas d'échec export DMP, les autres canaux (Doctolib, PDF) ne sont pas bloqués.

#### Sync Doctolib — P1
- **Description :** Synchronisation du compte-rendu validé vers la fiche patient Doctolib via API partenaire.
- **Critères d'acceptance :**
  - [ ] La sync Doctolib s'exécute en parallèle de l'export DMP (asyncio.gather).
  - [ ] Le médecin doit avoir configuré son token API Doctolib dans les paramètres.
  - [ ] En cas d'erreur Doctolib (API down, token expiré), une notification UI est affichée sans bloquer l'export DMP.

#### Génération PDF signé — P1
- **Description :** Export PDF du compte-rendu SOAP avec signature numérique médecin.
- **Critères d'acceptance :**
  - [ ] Le PDF généré contient les 4 sections SOAP formatées lisiblement.
  - [ ] Le PDF inclut le nom du médecin, la date, le numéro RPPS, et le logo cabinet si configuré.
  - [ ] Le PDF est téléchargeable immédiatement après validation.

---

### Module : Dossiers patients

#### Création et gestion dossier patient — P0
- **Description :** CRUD des dossiers patients avec données chiffrées et profil médical.
- **Critères d'acceptance :**
  - [ ] Un médecin peut créer un dossier patient avec : nom (chiffré), date de naissance (hashée), INS (si disponible).
  - [ ] Les allergies, traitements actifs et antécédents sont chiffrés AES-256-GCM au repos.
  - [ ] La liste des patients d'un cabinet est filtrée par cabinet_id (RLS).
  - [ ] Un médecin peut rechercher un patient par nom ou INS depuis son cabinet.
  - [ ] La mise à jour du DFG est possible depuis la fiche patient.

#### Historique consultations — P0
- **Description :** Accès aux consultations passées d'un patient avec SOAP validés.
- **Critères d'acceptance :**
  - [ ] La liste des consultations d'un patient est visible depuis sa fiche.
  - [ ] Un médecin ne peut consulter que les dossiers de son cabinet.
  - [ ] Les SOAP validés sont déchiffrés à la volée à l'affichage.

---

### Module : Abonnement & Facturation

#### Gestion abonnement Stripe (France) — P1
- **Description :** Souscription, gestion et annulation d'abonnement via Stripe.
- **Critères d'acceptance :**
  - [ ] Un médecin peut s'abonner depuis l'interface (Stripe Checkout).
  - [ ] 3 plans disponibles : Solo (1 médecin), Cabinet (2-10 médecins), Réseau (11+).
  - [ ] Le trial 14j se termine automatiquement et bascule sur plan payant si CB enregistrée.
  - [ ] Sans CB après trial, accès lecture seule (pas de nouvelle consultation).
  - [ ] Une facture PDF est générée automatiquement par Stripe à chaque renouvellement.
  - [ ] Le webhook Stripe met à jour le statut abonnement en base en < 30s.

---

### Module : Administration

#### Dashboard admin MédecinAI — P1
- **Description :** Interface interne pour gérer la base globale, surveiller la qualité RAG, et gérer les comptes.
- **Critères d'acceptance :**
  - [ ] Accessible uniquement aux utilisateurs avec role='admin_medecinai'.
  - [ ] Liste de tous les cabinets avec statut abonnement.
  - [ ] Métriques qualité RAG : scores moyens par namespace, latences p50/p95.
  - [ ] Gestion base globale : upload, liste, suppression documents.

---

## 6. Intégration IA & RAG

### Architecture RAG — Vue d'ensemble

**5 namespaces dans pgvector, tous dans la table `chunks` discriminée par le champ `source` :**

| Namespace | Source | Isolation | Fréquence sync |
|---|---|---|---|
| NS1 — CCAM | ATIH (codes actes) | Globale | Hebdomadaire |
| NS2 — Guidelines | HAS (recommandations) | Globale | Mensuelle |
| NS3 — Médicaments | BDPM + Thériaque (interactions) | Globale | Quotidienne |
| NS4 — Patient | Historique consultations | cabinet_id + patient_id | Temps réel |
| NS5 — Corpus médecin | CR validés par médecin | doctor_id | À chaque validation |

### Pipeline RAG — Recherche hybride

**Modèle embedding :** DrBERT-7GB-cased (768 dims, on-premise GPU)  
**Fallback MVP :** CamemBERT-bio (768 dims, on-premise)  
**Seuil pertinence :** Score cosinus minimum 0.65 pour inclure un chunk  
**BM25 :** Index BM25Okapi en RAM, reconstruit chaque nuit (~800 Mo pour 420 000 chunks)

**Étapes du pipeline (par consultation) :**

1. **Query enrichment** — transcript + spécialité médecin + profil patient (DFG, grossesse, traitements actifs, allergies) → requête enrichie.
2. **Embedding requête** — DrBERT encode la requête enrichie (normalisé).
3. **Recherche hybride parallèle** :
   - Dense : HNSW pgvector top-20, filtrés par namespaces autorisés + isolation patient.
   - Sparse : BM25Okapi top-20 sur même corpus.
4. **Fusion RRF** : Reciprocal Rank Fusion (k=60), poids adaptatifs selon type de requête (dense 0.7 / sparse 0.3 par défaut, sparse 0.7 si codes CCAM/CIM détectés).
5. **Cross-encoder reranking** : top-12 → cross-encoder mMiniLMv2 → top-5 reranqués.
6. **Medical boosting** : score × facteurs cliniques (×2.0 IRC, ×2.0 grossesse, ×1.8 interaction détectée, ×1.3 spécialité).
7. **MMR déduplication** : lambda=0.65, top-5 finaux diversifiés.
8. **Assemblage prompt** : 6 couches (system → safety context → RAG chunks → style médecin → transcript → instruction).
9. **Génération LLM** : Claude claude-sonnet-4-6, temperature=0.15, top_p=0.90, max_tokens=1500.
10. **Validation output** : Regex codes CCAM/CIM-10, JSON schema validation, confiance < 0.70 → alerte INFO.

### Format output SOAP (JSON)

```json
{
  "alerts": [
    {
      "type": "ALLERGIE | INTERACTION | DOSAGE | CLINIQUE",
      "severity": "CRITIQUE | ATTENTION | INFO",
      "message": "string",
      "drug": "string | null",
      "source": "string"
    }
  ],
  "soap": {
    "S": {
      "motif": "string",
      "plaintes": ["string"],
      "context": "string"
    },
    "O": {
      "constantes": {
        "TA": "string | null",
        "FC": "string | null",
        "SpO2": "string | null",
        "poids": "string | null",
        "IMC": "string | null"
      },
      "examen_clinique": "string",
      "resultats": ["string"]
    },
    "A": {
      "diagnostic_principal": {
        "libelle": "string",
        "cim10": "string | null"
      },
      "diagnostics_diff": [{"libelle": "string", "cim10": "string | null"}],
      "synthese": "string"
    },
    "P": {
      "prescriptions": [
        {
          "medicament": "string",
          "posologie": "string",
          "duree": "string",
          "ccam_code": "string | null",
          "interaction_flag": "boolean"
        }
      ],
      "examens": [{"libelle": "string", "ccam_code": "string | null"}],
      "arret_travail": {"duree": "string | null", "motif": "string | null"},
      "prochaine_consultation": "string | null",
      "messages_patient": ["string"]
    }
  },
  "metadata": {
    "confidence_score": "float",
    "missing_info": ["string"],
    "chunks_used": ["string"],
    "generated_at": "string"
  }
}
```

### Prompt système SOAP

```
Tu es un assistant médical de rédaction de comptes-rendus.
Tu travailles sous la supervision directe du médecin qui valide chaque compte-rendu avant tout usage clinique ou administratif.

RÈGLES ABSOLUES — ne jamais déroger :

1. FIDÉLITÉ : Ne rapporter que ce qui a été dit dans la consultation. Ne jamais inférer, compléter, ni inventer de données cliniques. Si une information SOAP est absente du transcript → "[non mentionné]"

2. ALLERGIES : Si une prescription est détectée pour une molécule listée dans les allergies patient → générer UNIQUEMENT alerts avec severity CRITIQUE. Ne pas générer soap.

3. INTERACTIONS : Si une nouvelle prescription interagit avec un traitement actif selon les références VIDAL fournies → signaler dans le bloc P avec niveau de sévérité.

4. CODES : Utiliser uniquement les codes CCAM et CIM-10 présents dans les références fournies. Ne jamais inventer un code.

5. FORMAT : Répondre uniquement en JSON valide. Aucun texte hors JSON.

6. INCERTITUDE : Si le transcript est ambigu sur un élément clinique important → [à confirmer avec le médecin].

Spécialité du médecin : {specialty}
Date de consultation : {date}
```

### Prompt système RAG (questions cliniques)

```
Tu es MédecinAI, un assistant médical expert destiné aux médecins.
Tu réponds uniquement en français.
Tu bases tes réponses exclusivement sur les documents médicaux fournis dans le contexte.
Si le contexte ne contient pas d'information suffisante, indique-le explicitement : "Aucun document pertinent trouvé dans votre base de connaissances."
Ne jamais inventer d'information médicale.
Cite toujours tes sources en indiquant le nom du document et la section.

Contexte médical :
{chunks}

Question : {question}
```

### Usage : Transcription Whisper

- **Modèle :** faster-whisper large-v3 (on-premise GPU)
- **Input :** Audio PCM 16kHz mono + initial_prompt contextuel (spécialité + médicaments actifs du patient)
- **Output :** `{text: string, words: [{word, start, end, probability}], language: "fr"}`
- **Streaming :** Chunks de 30s avec VAD (webrtcvad aggressiveness=2), flush sur silence > 15 frames

### Usage : Interactions médicamenteuses (explication)

- **Modèle :** Pas de LLM — lookup déterministe SQL sur table `drug_interactions`
- **Input :** `{new_drugs: [DCI], active_drugs: [DCI]}`
- **Output :** `[{drug_a, drug_b, severity, mechanism, consequence, management, alternative}]`
- **Note :** LLM utilisé uniquement pour la mise en forme narrative de l'alerte dans le SOAP, pas pour la détection.

### Index pgvector

```sql
-- HNSW par namespace (index partiels)
CREATE INDEX idx_hnsw_ccam  ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=128)  WHERE source = 'ccam';
CREATE INDEX idx_hnsw_has   ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=200)  WHERE source = 'has';
CREATE INDEX idx_hnsw_vidal ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=20, ef_construction=200)  WHERE source = 'vidal';
CREATE INDEX idx_hnsw_pat   ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=128)  WHERE source = 'patient_history';
CREATE INDEX idx_hnsw_doc   ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=128)  WHERE source = 'doctor_corpus';

-- Full-text search FR (composante BM25 hybride)
CREATE INDEX idx_fts ON chunks USING gin(to_tsvector('french', text));

-- Isolation patient
CREATE INDEX idx_patient_iso ON chunks (patient_id, doctor_id) WHERE source = 'patient_history';

-- ef_search en production
SET hnsw.ef_search = 100; -- CCAM/NS5 ; 200 pour HAS/VIDAL
```

---

## 7. Sécurité & conformité

### Rôles utilisateurs

| Rôle | Droits |
|---|---|
| `medecin` | CRUD ses propres consultations, patients, documents cabinet. Lecture base globale. |
| `admin_cabinet` | Droits médecin + gestion équipe cabinet (inviter/révoquer médecins). |
| `admin_medecinai` | Accès toutes données agrégées (anonymisées), gestion base globale, dashboard qualité. |

### Chiffrement

- **Données au repos :** AES-256-GCM pour tous les champs patient identifiants (nom, allergies, traitements, transcript).
- **Clé de chiffrement :** Clé maître stockée en HSM (Hardware Security Module OVHcloud). Clé par patient dérivée via HKDF depuis la clé maître + patient_id.
- **Vecteurs d'embedding :** Non chiffrés (nécessaire pour la recherche vectorielle). Constituent une empreinte non-inversible du texte.
- **Données en transit :** TLS 1.3 obligatoire sur toutes les connexions. HSTS activé.
- **Base de données :** PostgreSQL avec chiffrement disque OVHcloud + connexion SSL obligatoire.

### Isolation des données RAG

- **Row-Level Security PostgreSQL :** Activé sur `chunks` et `patients`. Policy restrictive par `patient_id` + `doctor_id`.
- **Filtre applicatif mandatory :** Classe `PatientVectorStore` avec filtre automatique — impossible d'oublier le filtre dans le code applicatif.
- **Pseudonymisation avant LLM :** Presidio détecte et remplace les PII avant tout envoi à l'API Anthropic. Table de correspondance token ↔ valeur réelle stockée serveur HDS uniquement (TTL = durée de session).
- **Cache Redis :** Jamais de cache pour les résultats `patient_history`. Cache autorisé pour CCAM (TTL 1h), HAS (TTL 5min).
- **Embedding on-premise obligatoire pour NS4 :** Les chunks patient ne peuvent pas passer par une API externe. DrBERT/CamemBERT déployés sur GPU OVHcloud HDS.

### Conformité France (RGPD)

- Données hébergées exclusivement sur OVHcloud HDS (Hébergement Données de Santé certifié).
- DPO externalisé nommé avant le lancement.
- Politique de confidentialité et CGU conformes RGPD affichées à l'inscription.
- Droit à l'effacement : suppression logique des données patient sur demande (conservation minimale légale 10 ans pour données médicales).
- Registre des traitements tenu à jour.
- Contrat DPA (Data Processing Agreement) signé avec OVHcloud et Anthropic.
- Logs d'accès retenus 10 ans minimum.

### Conformité Algérie (Loi 18-07)

- Données patients algériens hébergées sur serveur avec localisation conforme [À PRÉCISER — vérifier exigence localisation données loi 18-07].
- Consentement explicite collecté à l'inscription.
- Notification ANPDP obligatoire avant traitement.
- Langue interface : français (langue médicale officielle Algérie).

### Audit trail

- Table `audit_log` append-only (aucun UPDATE/DELETE sur cette table via contrainte PostgreSQL).
- Hash chaîné SHA-256 : chaque entrée contient le hash de la précédente (détection altération).
- Événements tracés : `soap_generated`, `soap_edited`, `alert_acknowledged`, `soap_signed`, `dmp_exported`, `doctolib_synced`, `export_failed`, `patient_data_accessed`, `document_uploaded`, `document_deleted`.
- Rétention : 10 ans minimum, cold storage OVHcloud à partir de 2 ans.

### Rate limiting

- API : 100 req/min par cabinet_id, 10 req/min sur endpoints embedding/LLM.
- Upload documents : 10 fichiers/heure par médecin.
- Tentatives connexion : blocage compte après 5 échecs (Auth0).

---

## 8. Variables d'environnement

```env
# ── Base de données ────────────────────────────────────────────────
DATABASE_URL=               # URL PostgreSQL complète avec pgvector activé
                            # ex: postgresql+asyncpg://user:pass@host:5432/medecinai
DATABASE_SSL=true           # Forcer SSL sur connexion PostgreSQL

# ── Redis ──────────────────────────────────────────────────────────
REDIS_URL=                  # URL Redis ex: redis://localhost:6379/0

# ── Auth0 ──────────────────────────────────────────────────────────
AUTH0_DOMAIN=               # Domaine Auth0 ex: medecinai.eu.auth0.com
AUTH0_CLIENT_ID=            # Client ID application web
AUTH0_CLIENT_SECRET=        # Client secret application web
AUTH0_AUDIENCE=             # Audience API ex: https://api.medecinai.fr
AUTH0_CALLBACK_URL=         # URL de callback OAuth2

# ── Pro Santé Connect (e-CPS) ──────────────────────────────────────
PSC_CLIENT_ID=              # Client ID Pro Santé Connect
PSC_CLIENT_SECRET=          # Client secret Pro Santé Connect
PSC_DISCOVERY_URL=          # URL discovery endpoint ANS

# ── Anthropic (LLM) ────────────────────────────────────────────────
ANTHROPIC_API_KEY=          # Clé API Anthropic pour Claude claude-sonnet-4-6
ANTHROPIC_MODEL=            # claude-sonnet-4-6
ANTHROPIC_MAX_TOKENS=1500   # Tokens max output SOAP
ANTHROPIC_TEMPERATURE=0.15  # Température génération médicale (quasi-déterministe)

# ── Modèles IA on-premise ──────────────────────────────────────────
EMBEDDING_MODEL_PATH=       # Chemin local DrBERT-7GB-cased ou CamemBERT-bio
EMBEDDING_MODEL_NAME=       # "DrBERT/DrBERT-7GB-cased" ou "almanach/camembert-bio"
EMBEDDING_DIMENSION=768     # Dimensions vecteur (DrBERT et CamemBERT-bio)
WHISPER_MODEL_SIZE=large-v3 # Taille modèle Whisper (large-v3 ou medium pour moindre GPU)
WHISPER_DEVICE=cuda         # "cuda" ou "cpu"
RERANKER_MODEL_PATH=        # Chemin cross-encoder/mmarco-mMiniLMv2-L12-H384-v1

# ── RAG pipeline ───────────────────────────────────────────────────
RAG_CHUNK_SIZE=512          # Taille cible chunks en tokens
RAG_CHUNK_OVERLAP=64        # Overlap entre chunks (tokens)
RAG_TOP_K_RETRIEVAL=20      # Candidats après recherche hybride (avant reranking)
RAG_TOP_K_FINAL=5           # Chunks injectés dans le prompt après reranking
RAG_MIN_SCORE=0.65          # Score cosinus minimum pour inclure un chunk
RAG_RRF_K=60                # Constante k pour Reciprocal Rank Fusion
RAG_MMR_LAMBDA=0.65         # Lambda MMR (0=diversité max, 1=pertinence max)
BM25_INDEX_PATH=            # Chemin fichier index BM25 sérialisé (pickle)
HNSW_EF_SEARCH=100          # ef_search HNSW (100 CCAM/NS5, 200 HAS/VIDAL)

# ── Chiffrement patient ────────────────────────────────────────────
PATIENT_ENCRYPTION_MASTER_KEY= # Clé maître AES-256 hex 64 chars — JAMAIS en clair en prod
                                # En production : charger depuis HSM OVHcloud

# ── Stripe ─────────────────────────────────────────────────────────
STRIPE_SECRET_KEY=          # Clé secrète Stripe (sk_live_... en prod)
STRIPE_PUBLISHABLE_KEY=     # Clé publique Stripe (pk_live_... en prod)
STRIPE_WEBHOOK_SECRET=      # Secret webhook Stripe pour vérification signatures
STRIPE_PRICE_SOLO=          # Price ID Stripe plan Solo
STRIPE_PRICE_CABINET=       # Price ID Stripe plan Cabinet
STRIPE_PRICE_RESEAU=        # Price ID Stripe plan Réseau

# ── MSSanté / DMP ──────────────────────────────────────────────────
MSSANTE_GATEWAY_URL=        # URL API MSSanté ex: https://gateway.mssante.fr/dmp/v1
MEDECINAI_CERT_FINGERPRINT= # Empreinte certificat logiciel MédecinAI (ANS)

# ── Doctolib API ───────────────────────────────────────────────────
DOCTOLIB_API_BASE_URL=      # https://api.doctolib.fr/v2 (après certification partenaire)

# ── ATIH / HAS / BDPM ──────────────────────────────────────────────
ATIH_CCAM_URL=              # URL téléchargement CCAM ATIH
HAS_API_BASE=               # URL API catalogue HAS
BDPM_API_URL=               # URL API BDPM data.gouv.fr
THERIAQUE_API_URL=          # URL API Thériaque (interactions gratuites)

# ── Application ────────────────────────────────────────────────────
APP_ENV=                    # "development" | "staging" | "production"
APP_SECRET_KEY=             # Clé secrète application (sessions, CSRF)
FRONTEND_URL=               # URL frontend ex: https://app.medecinai.fr
BACKEND_URL=                # URL backend ex: https://api.medecinai.fr
ALLOWED_ORIGINS=            # CORS origins séparées par virgule

# ── Celery ─────────────────────────────────────────────────────────
CELERY_BROKER_URL=          # URL RabbitMQ ex: amqp://user:pass@localhost:5672/
CELERY_RESULT_BACKEND=      # URL Redis pour résultats Celery

# ── Emails transactionnels ─────────────────────────────────────────
SMTP_HOST=                  # Hôte SMTP
SMTP_PORT=587               # Port SMTP
SMTP_USER=                  # Utilisateur SMTP
SMTP_PASSWORD=              # Mot de passe SMTP
EMAIL_FROM=                 # Adresse expéditeur ex: noreply@medecinai.fr

# ── Monitoring ─────────────────────────────────────────────────────
PROMETHEUS_PORT=9090        # Port exposition métriques Prometheus
SENTRY_DSN=                 # DSN Sentry pour capture erreurs (optionnel)
```

---

## 9. Roadmap

### MVP — Semaines 1-4
> Objectif : valider que les médecins utilisent le produit quotidiennement et que la transcription + SOAP fonctionne sur de vraies consultations.

- [ ] Setup infrastructure OVHcloud HDS : PostgreSQL + pgvector + Redis + GPU T4.
- [ ] Activation Row-Level Security et schéma BDD complet avec migrations Alembic.
- [ ] Pipeline Whisper large-v3 on-premise avec VAD et streaming WebSocket.
- [ ] Modèle CamemBERT-bio déployé (fallback MVP avant DrBERT).
- [ ] Indexation initiale base globale : CCAM (8 700 actes), fiches mémo HAS prioritaires (200 docs), BDPM + Thériaque interactions (~180 000 paires).
- [ ] Pipeline RAG hybride (dense + BM25 + RRF) fonctionnel avec 5 namespaces.
- [ ] Génération SOAP via Claude claude-sonnet-4-6 avec safety context block et alertes allergies/interactions.
- [ ] Lookup déterministe interactions médicamenteuses (table `drug_interactions` + 200 entrées normalisation DCI→nom commercial).
- [ ] Interface médecin : démarrer consultation, transcription live, affichage SOAP, validation inline.
- [ ] Auth0 + inscription médecin + trial 14 jours.
- [ ] Chiffrement AES-256-GCM données patient + pseudonymisation Presidio avant API Anthropic.
- [ ] Audit trail immuable (hash chaîné).
- [ ] 3 médecins design partners actifs avec feedback hebdomadaire.

### V1 — Mois 2-3
> Objectif : 50 abonnés payants, export DMP opérationnel, flywheel NS5 actif.

- [ ] Migration vers DrBERT-7GB-cased (remplacement CamemBERT-bio, +5% précision retrieval).
- [ ] Cross-encoder reranker + medical boosting (IRC, grossesse, interactions) actif.
- [ ] MMR déduplication (diversité top-5 chunks).
- [ ] Export DMP via MSSanté (certification e-CPS obtenue).
- [ ] Sync Doctolib (certification partenaire Doctolib obtenue).
- [ ] Génération PDF signé.
- [ ] Flywheel NS5 : capture diffs validation → indexation DoctorStyleChunk → few-shot dans prompt.
- [ ] Dashboard médecin : courbe qualité SOAP, exemples de style appris, motifs couverts.
- [ ] Interface upload documents privés cabinet (PDF/DOCX → indexation NS4 privé).
- [ ] Interface admin base globale : upload HAS complet (3 200 docs), sync VIDAL quotidienne.
- [ ] Stripe intégration complète (3 plans + webhooks + factures).
- [ ] Alertes dosage DFG (metformine, autres médicaments à élimination rénale).
- [ ] Déduplication interactions via canonicalisation paires (drug_a < drug_b).
- [ ] Sync ATIH hebdomadaire automatique (cron Celery).

### V2 — Mois 4+
> Objectif : scalabilité, spécialités, fine-tuning, expansion Algérie.

- [ ] Fine-tuning CamemBERT-bio/DrBERT sur corpus propriétaire (~100 000 paires training).
- [ ] Pipeline évaluation automatique régression (detect_model_quality sur training pairs).
- [ ] Support spécialités : cardiologie, pneumologie, psychiatrie, kinésithérapie — vocabulaires Whisper dédiés + boosting RAG.
- [ ] Intégration INSi ANS pour récupération INS certifié.
- [ ] Module ordonnances structurées avec export HL7 FHIR MedicationRequest.
- [ ] Programme ambassador : tableau de bord parrainage + tracking conversions.
- [ ] Calculateur ROI public (outil acquisition SEO).
- [ ] Support Algérie : conformité loi 18-07 complète, hébergement local [À PRÉCISER], intégration référentiels MSPRH dans base globale.
- [ ] API publique pour intégration logiciels métier tiers (Hellodoc, Maiia).
- [ ] Module analytics cabinet : statistiques consultations, motifs fréquents, temps économisé.
- [ ] Application mobile React Native (iOS + Android) pour consultation hors cabinet.

---

## 10. Commandes de démarrage

```bash
# ── Prérequis ──────────────────────────────────────────────────────
# Python 3.12+, Node.js 20+, Docker, PostgreSQL 16, Redis 7
# GPU CUDA disponible pour Whisper et les modèles d'embedding

# ── Cloner le projet ───────────────────────────────────────────────
git clone https://github.com/[org]/medecinai.git
cd medecinai
cp .env.example .env
# Remplir toutes les variables dans .env avant de continuer

# ── Installation Backend (FastAPI) ────────────────────────────────
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt --break-system-packages

# ── Installation Frontend (Next.js) ───────────────────────────────
cd ../frontend
npm install

# ── Base de données ────────────────────────────────────────────────
# Activer pgvector
psql -d medecinai -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -d medecinai -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# Activer Row-Level Security
psql -d medecinai -f shared/scripts/setup_rls.sql

# Migrations Alembic
cd backend
alembic upgrade head

# ── Téléchargement modèles IA ──────────────────────────────────────
# CamemBERT-bio (MVP embedding)
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('almanach/camembert-bio')"

# Cross-encoder reranker
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"

# Whisper large-v3 via faster-whisper
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')"

# ── Indexation base globale initiale ──────────────────────────────
cd backend
# Import CCAM (téléchargement + indexation ~18min sur GPU)
python -m app.jobs.sync_ccam

# Import fiches mémo HAS prioritaires
python -m app.jobs.sync_has --mode=memo-only

# Import BDPM + Thériaque interactions
python -m app.jobs.sync_vidal

# Construction index BM25 initial
python -m ia.rag.retriever.bm25_index --rebuild

# ── Lancer en développement ────────────────────────────────────────
# Terminal 1 — Backend FastAPI
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — Workers Celery
cd backend
celery -A app.jobs.celery_app worker --loglevel=info

# Terminal 3 — Frontend Next.js
cd frontend
npm run dev

# Terminal 4 — Redis (si non démarré via Docker)
redis-server

# ── Tests ──────────────────────────────────────────────────────────
# Backend
cd backend
pytest tests/ -v --cov=app

# Frontend
cd frontend
npm run test

# Test pipeline RAG complet
cd backend
python -m tests.integration.test_rag_pipeline

# ── Build production ───────────────────────────────────────────────
# Frontend
cd frontend
npm run build

# Backend (Docker)
docker build -t medecinai-backend:latest ./backend

# ── Déploiement ────────────────────────────────────────────────────
# Frontend → Vercel
cd frontend
npx vercel --prod

# Backend → OVHcloud HDS (Docker + Kubernetes ou VM dédiée)
docker push registry.ovhcloud.com/medecinai/backend:latest
kubectl apply -f k8s/backend-deployment.yaml  # si Kubernetes
# OU
ssh deploy@hds.medecinai.fr "cd /opt/medecinai && docker-compose up -d"

# ── Vérification santé ─────────────────────────────────────────────
bash shared/scripts/health_check.sh
# Vérifie : PostgreSQL + pgvector, Redis, modèles IA chargés,
#           index HNSW présents, BM25 en mémoire, API Anthropic joignable

# ── Cron jobs production (Celery Beat) ────────────────────────────
# Sync ATIH CCAM — tous les lundis 2h00
# 0 2 * * 1 → celery task sync_ccam

# Sync HAS — 1er du mois 3h00
# 0 3 1 * * → celery task sync_has

# Sync BDPM/Thériaque — tous les jours 4h00
# 0 4 * * * → celery task sync_vidal

# Rebuild BM25 index — tous les jours 5h00
# 0 5 * * * → celery task rebuild_bm25

# Purge NS5 > 6 mois — tous les dimanches 6h00
# 0 6 * * 0 → celery task purge_old_style_chunks
```

---

*Document généré à partir du brainstorming produit MédecinAI — 90 jours, stack technique, architecture RAG, acquisition, pipeline complet transcription → SOAP → export DMP. Toutes les décisions sont tracées dans la conversation source. Les éléments marqués [À PRÉCISER] nécessitent une décision avant implémentation.*
