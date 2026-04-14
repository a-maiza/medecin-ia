"""SOAP generation service: full pipeline orchestration.

Pipeline (per REQUIREMENTS.md §6):
    1. Load consultation + patient + medecin from DB
    2. Decrypt patient context (allergies, traitements, antecedents)
    3. Pseudonymise transcript before any LLM call
    4. Query enrichment
    5. Hybrid RAG retrieval (parallel dense + BM25)
    6. Cross-encoder reranking → top-12
    7. Medical boosting
    8. MMR deduplication → top-5
    9. Interaction check (deterministic SQL < 5ms)
    10. CI_ABSOLUE guard — blocks SOAP, returns only alerts
    11. Prompt assembly (6 layers)
    12. Claude claude-sonnet-4-6 streaming (temperature=0.15, top_p=0.90, max_tokens=1500)
    13. Output validation (JSON schema + code traceability)
    14. Persist soap_generated + chunks_used + alerts to DB
    15. Yield tokens to caller for WebSocket streaming

Usage (from router):
    async for token in soap_generator.generate(consultation_id, db, redis, current_user):
        await ws.send_text(token)
"""
from __future__ import annotations

import datetime
import json
import logging
import uuid
from typing import AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.consultation import Consultation
from app.models.medecin import Medecin
from app.models.patient import Patient
from app.schemas.soap import SoapAlert, SoapGenerateResponse

log = logging.getLogger(__name__)

_SETTINGS = None  # lazy


def _settings():
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = get_settings()
    return _SETTINGS


# ── Interaction checker helper (inlined here to avoid circular import) ─────────

async def _check_interactions(
    db: AsyncSession,
    drug_names: list[str],
) -> list[object]:
    """Deterministic SQL lookup on drug_interactions table.

    Returns list of DrugInteraction ORM objects sorted by severity descending.
    Called before SOAP generation — must be < 5 ms.
    """
    if len(drug_names) < 2:
        return []

    from app.models.drug_interaction import DrugInteraction

    normalised = [d.strip().lower() for d in drug_names]

    from sqlalchemy import or_, and_, text
    # Build all (a, b) pairs with canonical ordering (a < b alphabetically)
    pairs: list[tuple[str, str]] = []
    for i in range(len(normalised)):
        for j in range(i + 1, len(normalised)):
            a, b = sorted([normalised[i], normalised[j]])
            pairs.append((a, b))

    if not pairs:
        return []

    # Build OR of AND conditions for each pair
    conditions = [
        and_(DrugInteraction.drug_a == a, DrugInteraction.drug_b == b)
        for a, b in pairs
    ]
    from sqlalchemy import or_
    stmt = select(DrugInteraction).where(or_(*conditions)).order_by(
        DrugInteraction.severity  # CI_ABSOLUE < CI_RELATIVE < PRECAUTION alphabetically
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Cosine similarity for quality score ───────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Main generator ────────────────────────────────────────────────────────────

class SoapGenerator:
    """Stateless service — instantiate once per application."""

    async def generate(
        self,
        consultation_id: uuid.UUID,
        db: AsyncSession,
        redis,                    # aioredis.Redis instance
        current_user_id: uuid.UUID,
        cabinet_id: uuid.UUID,
        *,
        clinical_justification: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream SOAP tokens to the caller.

        Yields JSON strings:
            {"type": "alert",  "data": {...}}
            {"type": "token",  "data": "<partial SOAP text>"}
            {"type": "done",   "data": {"soap": {...}, "metadata": {...}}}
            {"type": "error",  "data": {"message": "..."}}
            {"type": "blocked","data": {"alerts": [...]}}  ← CI_ABSOLUE
        """
        from app.security.rls import rls_context
        from app.security.encryption import decrypt
        from app.security.pseudonymizer import pseudonymize
        from app.security.audit import log_event
        from ia.rag.retriever.query_enricher import enrich_query
        from ia.rag.retriever.hybrid_search import hybrid_search, GLOBAL_NAMESPACES
        from ia.rag.reranker.cross_encoder import rerank_and_sort
        from ia.rag.reranker.medical_booster import boost
        from ia.rag.reranker.mmr import mmr
        from ia.soap.prompt_assembler import assemble_soap_prompt
        from ia.soap.output_validator import validate_soap_output
        from ia.embedding.service import get_embedding_service

        # ── 1. Load consultation ───────────────────────────────────────────────
        consultation = await db.get(Consultation, consultation_id)
        if consultation is None:
            yield json.dumps({"type": "error", "data": {"message": "Consultation not found"}})
            return
        if str(consultation.cabinet_id) != str(cabinet_id):
            yield json.dumps({"type": "error", "data": {"message": "Forbidden"}})
            return

        patient = await db.get(Patient, consultation.patient_id)
        medecin = await db.get(Medecin, consultation.medecin_id)
        if patient is None or medecin is None:
            yield json.dumps({"type": "error", "data": {"message": "Patient or medecin not found"}})
            return

        # ── 2. Decrypt patient context ─────────────────────────────────────────
        patient_id = patient.id
        allergies: list[str] = []
        active_drugs: list[str] = []
        antecedents: list[str] = []

        try:
            if patient.allergies_encrypted:
                allergies = json.loads(decrypt(patient.allergies_encrypted, patient_id))
            if patient.traitements_actifs_encrypted:
                active_drugs = json.loads(decrypt(patient.traitements_actifs_encrypted, patient_id))
            if patient.antecedents_encrypted:
                antecedents = json.loads(decrypt(patient.antecedents_encrypted, patient_id))
        except Exception as exc:
            log.warning("[soap] Decryption error for patient %s: %s", patient_id, exc)

        # ── 3. Decrypt + pseudonymise transcript ───────────────────────────────
        transcript_raw = ""
        if consultation.transcript_encrypted:
            try:
                transcript_raw = decrypt(consultation.transcript_encrypted, patient_id)
            except Exception as exc:
                log.error("[soap] Cannot decrypt transcript: %s", exc)
                yield json.dumps({"type": "error", "data": {"message": "Cannot decrypt transcript"}})
                return

        session_id = f"soap:{consultation_id}"
        transcript_pseudo = await pseudonymize(transcript_raw, session_id, redis)

        # ── 4. Query enrichment ────────────────────────────────────────────────
        enriched = enrich_query(
            consultation.motif,
            specialty=medecin.specialite,
            active_drugs=active_drugs,
            allergies=allergies,
            antecedents=antecedents,
            dfg=patient.dfg,
            is_pregnant=patient.grossesse,
        )

        # ── 5. Hybrid RAG retrieval ────────────────────────────────────────────
        namespaces = list(GLOBAL_NAMESPACES) + ["patient_history"]
        async with rls_context(db, cabinet_id=cabinet_id, patient_id=patient_id):
            raw_hits = await hybrid_search(
                db,
                enriched,
                namespaces,
                top_k=20,
                cabinet_id=cabinet_id,
                patient_id=patient_id,
            )

        # ── 6. Cross-encoder reranking ─────────────────────────────────────────
        passages = [h.content for h in raw_hits if h.content]
        reranked = rerank_and_sort(enriched.raw_query, passages, top_k=12)
        # Reattach metadata from raw_hits to reranked results
        content_to_hit = {h.content: h for h in raw_hits if h.content}
        reranked_hits = [content_to_hit.get(text) for _, text in reranked if content_to_hit.get(text)]

        # ── 7. Medical boosting ────────────────────────────────────────────────
        # Interaction check must run first (its result feeds boosting)
        all_drugs = list(set(active_drugs))  # interaction check on current meds
        interactions = await _check_interactions(db, all_drugs)

        has_ci_absolue = any(
            getattr(ia, "severity", "") == "CI_ABSOLUE" for ia in interactions
        )
        has_interaction = len(interactions) > 0

        # Build RankedPassage objects for boosting
        from ia.rag.reranker.cross_encoder import RankedPassage
        ranked_for_boost = [
            RankedPassage(text=text, score=score, original_index=i)
            for i, (score, text) in enumerate(reranked)
        ]

        boosted = boost(
            ranked_for_boost,
            specialty=medecin.specialite,
            dfg=patient.dfg,
            is_pregnant=patient.grossesse,
            has_interaction=has_interaction,
        )

        # ── 8. MMR deduplication → top-5 ─────────────────────────────────────
        svc = get_embedding_service()
        boosted_texts = [b.text for b in boosted]
        boosted_scores = [b.boosted_score for b in boosted]

        final_chunks: list[object] = []
        if boosted_texts:
            import asyncio
            loop = asyncio.get_event_loop()
            candidate_vecs = await loop.run_in_executor(None, svc.embed, boosted_texts)
            query_vec = await loop.run_in_executor(None, svc.embed_single, enriched.text)

            from dataclasses import dataclass
            @dataclass
            class _BoostedForMMR:
                text: str
                score: float
                chunk_id: str = ""
                source: str = ""
                metadata: dict = None  # type: ignore

            mmr_candidates = [
                _BoostedForMMR(
                    text=b.text,
                    score=b.boosted_score,
                    chunk_id=str(getattr(content_to_hit.get(b.text), "chunk_id", "")),
                    source=getattr(content_to_hit.get(b.text), "source", ""),
                    metadata=getattr(content_to_hit.get(b.text), "metadata", {}) or {},
                )
                for b in boosted
            ]

            selected = mmr(mmr_candidates, query_vec, candidate_vecs, top_k=5)
            final_chunks = selected

        chunk_ids_used = [getattr(c, "chunk_id", "") for c in final_chunks]

        # ── 9. CI_ABSOLUE guard ────────────────────────────────────────────────
        alert_objs: list[dict] = []
        for ia in interactions:
            alert_objs.append({
                "type": "INTERACTION",
                "severity": getattr(ia, "severity", "ATTENTION"),
                "message": (
                    f"{getattr(ia, 'drug_a', '?')} × {getattr(ia, 'drug_b', '?')}: "
                    f"{getattr(ia, 'consequence', '') or getattr(ia, 'mechanism', '')}"
                ),
                "drug": f"{getattr(ia, 'drug_a', '')} + {getattr(ia, 'drug_b', '')}",
                "source": getattr(ia, "source", "VIDAL"),
            })
            yield json.dumps({"type": "alert", "data": alert_objs[-1]})

        if has_ci_absolue and not clinical_justification:
            # Block SOAP generation
            await self._persist_blocked(db, consultation, alert_objs, chunk_ids_used)
            await log_event(
                db,
                cabinet_id=cabinet_id,
                medecin_id=current_user_id,
                action="soap_blocked_ci_absolue",
                resource_type="consultation",
                resource_id=str(consultation_id),
                payload={"alerts": alert_objs},
            )
            yield json.dumps({"type": "blocked", "data": {"alerts": alert_objs}})
            return

        # ── 10. Fetch NS5 style examples ───────────────────────────────────────
        from app.models.doctor_style import DoctorStyleChunk
        motif_key = consultation.motif[:50].lower()
        style_result = await db.execute(
            select(DoctorStyleChunk)
            .where(
                DoctorStyleChunk.medecin_id == current_user_id,
                DoctorStyleChunk.motif_key == motif_key,
            )
            .order_by(DoctorStyleChunk.quality_score.desc())
            .limit(3)
        )
        style_examples = list(style_result.scalars().all())

        # ── 11. Prompt assembly ────────────────────────────────────────────────
        assembled = assemble_soap_prompt(
            specialty=medecin.specialite,
            date=consultation.date.date().isoformat() if consultation.date else None,
            allergies=allergies,
            interactions=interactions,
            rag_chunks=final_chunks,
            style_examples=style_examples,
            transcript=transcript_pseudo,
        )

        # ── 12. Claude streaming ───────────────────────────────────────────────
        import anthropic

        settings = _settings()
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        full_text = ""
        try:
            async with client.messages.stream(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=1500,
                temperature=0.15,
                top_p=0.90,
                system=assembled.system,
                messages=assembled.messages,
            ) as stream:
                async for text_delta in stream.text_stream:
                    full_text += text_delta
                    yield json.dumps({"type": "token", "data": text_delta})

        except Exception as exc:
            log.error("[soap] Claude streaming error: %s", exc)
            yield json.dumps({"type": "error", "data": {"message": f"LLM error: {exc}"}})
            return

        # ── 13. Output validation ──────────────────────────────────────────────
        validation = validate_soap_output(full_text, allowed_chunk_ids=chunk_ids_used)

        if not validation.is_valid:
            log.warning(
                "[soap] Validation failed for consultation %s: %s",
                consultation_id,
                validation.errors,
            )
            # Yield as error but still persist the raw text for debugging
            yield json.dumps({
                "type": "error",
                "data": {
                    "message": "SOAP validation failed",
                    "errors": validation.errors,
                    "raw": full_text[:500],
                },
            })
            return

        soap_dict = validation.soap_dict

        # Merge runtime alerts into the SOAP alerts array
        if alert_objs:
            soap_dict.setdefault("alerts", [])
            soap_dict["alerts"] = alert_objs + soap_dict["alerts"]

        # ── 14. Persist to DB ─────────────────────────────────────────────────
        await self._persist_generated(
            db,
            consultation,
            soap_dict,
            chunk_ids_used=chunk_ids_used,
            alerts=soap_dict.get("alerts", []),
        )

        await log_event(
            db,
            cabinet_id=cabinet_id,
            medecin_id=current_user_id,
            action="soap_generated",
            resource_type="consultation",
            resource_id=str(consultation_id),
            payload={
                "confidence_score": validation.confidence_score,
                "chunks_used_count": len(chunk_ids_used),
                "low_confidence": validation.low_confidence,
            },
        )

        # ── 15. Final done event ──────────────────────────────────────────────
        yield json.dumps({
            "type": "done",
            "data": {
                "soap": soap_dict.get("soap"),
                "metadata": soap_dict.get("metadata"),
                "alerts": soap_dict.get("alerts", []),
            },
        })

    async def _persist_generated(
        self,
        db: AsyncSession,
        consultation: Consultation,
        soap_dict: dict,
        *,
        chunk_ids_used: list[str],
        alerts: list[dict],
    ) -> None:
        consultation.soap_generated = soap_dict
        consultation.chunks_used = chunk_ids_used
        consultation.alerts = {"alerts": alerts}
        consultation.status = "generated"
        await db.commit()

    async def _persist_blocked(
        self,
        db: AsyncSession,
        consultation: Consultation,
        alerts: list[dict],
        chunk_ids_used: list[str],
    ) -> None:
        consultation.soap_generated = None
        consultation.alerts = {"alerts": alerts}
        consultation.chunks_used = chunk_ids_used
        consultation.status = "generated"  # status still advances so UI reacts
        await db.commit()

    async def validate_soap(
        self,
        consultation_id: uuid.UUID,
        validated_soap: dict,
        db: AsyncSession,
        current_user_id: uuid.UUID,
        cabinet_id: uuid.UUID,
        *,
        time_to_validate_seconds: Optional[float] = None,
    ) -> dict:
        """Persist validated SOAP, compute quality score, trigger NS5 indexing.

        Returns dict with quality_score and ns5_indexed.
        """
        from app.models.metrics import ValidationMetric, TrainingPair
        from app.security.audit import log_event

        consultation = await db.get(Consultation, consultation_id)
        if consultation is None or str(consultation.cabinet_id) != str(cabinet_id):
            raise ValueError("Consultation not found or forbidden")

        generated = consultation.soap_generated or {}

        # Compute quality score: cosine similarity between generated and validated
        # embeddings of the full JSON strings
        quality_score = await self._compute_quality_score(generated, validated_soap)

        # Determine correction types from diff
        correction_types = _diff_correction_types(generated, validated_soap)

        # Persist validated SOAP
        consultation.soap_validated = validated_soap
        consultation.quality_score = quality_score
        consultation.correction_types = correction_types
        consultation.status = "validated"

        # Create ValidationMetric
        metric = ValidationMetric(
            consultation_id=consultation_id,
            medecin_id=current_user_id,
            quality_score=quality_score,
            correction_types=correction_types,
            time_to_validate_seconds=time_to_validate_seconds,
        )
        db.add(metric)

        # Create TrainingPair for future fine-tuning
        if generated:
            pair = TrainingPair(
                consultation_id=consultation_id,
                medecin_id=current_user_id,
                raw_soap=generated,
                corrected_soap=validated_soap,
                diff_summary=", ".join(correction_types) if correction_types else None,
                tags=[consultation.motif[:30]] if consultation.motif else None,
            )
            db.add(pair)

        await db.commit()

        # NS5 indexing if quality_score > 0.7
        ns5_indexed = False
        if quality_score > 0.7:
            try:
                await self._index_ns5(
                    db,
                    consultation=consultation,
                    validated_soap=validated_soap,
                    quality_score=quality_score,
                    medecin_id=current_user_id,
                )
                ns5_indexed = True
            except Exception as exc:
                log.warning("[soap] NS5 indexing failed: %s", exc)

        await log_event(
            db,
            cabinet_id=cabinet_id,
            medecin_id=current_user_id,
            action="soap_signed",
            resource_type="consultation",
            resource_id=str(consultation_id),
            payload={
                "quality_score": quality_score,
                "correction_types": correction_types,
                "ns5_indexed": ns5_indexed,
            },
        )

        return {"quality_score": quality_score, "ns5_indexed": ns5_indexed}

    async def _compute_quality_score(
        self,
        generated: dict,
        validated: dict,
    ) -> float:
        """Cosine similarity between embeddings of generated and validated SOAP JSON."""
        import asyncio
        from ia.embedding.service import get_embedding_service

        svc = get_embedding_service()
        gen_text = json.dumps(generated, ensure_ascii=False)
        val_text = json.dumps(validated, ensure_ascii=False)

        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(None, svc.embed, [gen_text, val_text])
        if len(vecs) < 2:
            return 1.0

        return float(_cosine_similarity(vecs[0], vecs[1]))

    async def _index_ns5(
        self,
        db: AsyncSession,
        consultation: Consultation,
        validated_soap: dict,
        quality_score: float,
        medecin_id: uuid.UUID,
    ) -> None:
        """Index the validated SOAP into NS5 (doctor_corpus) for few-shot retrieval."""
        import asyncio
        from ia.embedding.service import get_embedding_service
        from app.models.doctor_style import DoctorStyleChunk

        soap_text = json.dumps(validated_soap, ensure_ascii=False)
        motif_key = (consultation.motif or "")[:50].lower()

        svc = get_embedding_service()
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(None, svc.embed, [soap_text])
        embedding = vecs[0] if vecs else None

        chunk = DoctorStyleChunk(
            medecin_id=medecin_id,
            consultation_id=consultation.id,
            motif_key=motif_key,
            text=soap_text[:2000],  # store truncated for few-shot
            quality_score=quality_score,
        )
        db.add(chunk)
        await db.flush()

        # Store embedding in chunks table (NS5)
        if embedding:
            from sqlalchemy import text as sa_text
            await db.execute(
                sa_text(
                    """
                    INSERT INTO chunks (id, source, content, embedding, metadata)
                    VALUES (:id, 'doctor_corpus', :content, CAST(:emb AS vector), :meta::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": str(chunk.id),
                    "content": soap_text[:2000],
                    "emb": str(embedding),
                    "meta": json.dumps({
                        "doctor_id": str(medecin_id),
                        "motif_key": motif_key,
                        "quality_score": quality_score,
                    }),
                },
            )
        await db.commit()


def _diff_correction_types(generated: dict, validated: dict) -> list[str]:
    """Heuristic: detect which SOAP sections were modified."""
    types: list[str] = []
    gen_soap = generated.get("soap", {})
    val_soap = validated.get("soap", {}) if isinstance(validated, dict) else validated

    for section in ("S", "O", "A", "P"):
        if json.dumps(gen_soap.get(section), sort_keys=True) != json.dumps(
            val_soap.get(section), sort_keys=True
        ):
            types.append(f"section_{section}_modified")

    return types


# ── Module-level singleton ─────────────────────────────────────────────────────
soap_generator = SoapGenerator()
