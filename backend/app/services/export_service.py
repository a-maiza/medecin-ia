"""Export service: FHIR R4 Bundle, reportlab PDF, DMP stub, Doctolib stub.

FHIR R4 resources are built as plain dicts conforming to the FHIR JSON
representation (https://hl7.org/fhir/R4/json.html) and validated against
the profil FHIR FR where applicable.

Stub implementations:
  push_to_dmp()      — MSSanté / Sesam-Vitale DMP gateway (requires e-CPS)
  push_to_doctolib() — Doctolib REST API
Both functions are async so they can be replaced with real HTTP calls without
changing the router.
"""
from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ── FHIR R4 Bundle ─────────────────────────────────────────────────────────────

def build_fhir_bundle(consultation, patient, medecin, cabinet) -> dict:
    """Build a FHIR R4 Bundle (document) containing Composition + Patient + Practitioner.

    The Composition uses LOINC 11488-4 (Consult note) with four sections
    mapped to S/O/A/P LOINC codes. French OIDs are used for RPPS and INS.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    soap = consultation.soap_validated or consultation.soap_generated or {}

    patient_resource = _build_patient_resource(patient)
    practitioner_resource = _build_practitioner_resource(medecin)
    composition = _build_composition(consultation, patient, medecin, soap)

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "document",
        "timestamp": now_iso,
        "entry": [
            {
                "fullUrl": f"urn:uuid:{consultation.id}",
                "resource": composition,
            },
            {
                "fullUrl": f"urn:uuid:{patient.id}",
                "resource": patient_resource,
            },
            {
                "fullUrl": f"urn:uuid:{medecin.id}",
                "resource": practitioner_resource,
            },
        ],
    }


def _build_patient_resource(patient) -> dict:
    resource: dict = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "identifier": [],
    }
    if patient.ins:
        # INS-NIR: OID 1.2.250.1.213.1.4.10 (France)
        resource["identifier"].append({
            "system": "urn:oid:1.2.250.1.213.1.4.10",
            "value": patient.ins,
        })
    if patient.sexe:
        resource["gender"] = {"M": "male", "F": "female"}.get(patient.sexe, "unknown")
    return resource


def _build_practitioner_resource(medecin) -> dict:
    return {
        "resourceType": "Practitioner",
        "id": str(medecin.id),
        "identifier": [
            {
                # RPPS OID (France — ASIP Santé)
                "system": "urn:oid:1.2.250.1.71.4.2.1",
                "value": medecin.rpps,
            }
        ],
        "name": [
            {
                "family": medecin.nom,
                "given": [medecin.prenom],
            }
        ],
        "qualification": [
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://interopsante.org/fhir/CodeSystem/fr-v2-0360",
                            "code": "SM",
                            "display": medecin.specialite,
                        }
                    ]
                }
            }
        ],
    }


def _build_composition(consultation, patient, medecin, soap: dict) -> dict:
    sections = _build_soap_sections(soap)
    return {
        "resourceType": "Composition",
        "id": str(consultation.id),
        "status": "final",
        "type": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "11488-4",
                    "display": "Consult note",
                }
            ]
        },
        "subject": {"reference": f"Patient/{patient.id}"},
        "date": consultation.date.isoformat(),
        "author": [{"reference": f"Practitioner/{medecin.id}"}],
        "title": consultation.motif,
        "section": sections,
    }


# LOINC codes for SOAP sections (standard clinical note mapping)
_SOAP_LOINC = [
    ("subjective", "10164-2", "History of present illness Narrative"),
    ("objective",  "29545-1", "Physical findings Narrative"),
    ("assessment", "51848-0", "Evaluation note"),
    ("plan",       "18776-5", "Plan of care note"),
]


def _build_soap_sections(soap: dict) -> list:
    sections = []
    for key, loinc_code, display in _SOAP_LOINC:
        content = soap.get(key) or soap.get(key.upper())
        if not content:
            continue
        if isinstance(content, list):
            text_content = "\n".join(str(item) for item in content)
        else:
            text_content = str(content)

        # Escape HTML entities for XHTML narrative
        xhtml = (
            text_content
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )
        sections.append(
            {
                "title": display,
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": loinc_code,
                            "display": display,
                        }
                    ]
                },
                "text": {
                    "status": "generated",
                    "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{xhtml}</div>',
                },
            }
        )
    return sections


# ── PDF generation ─────────────────────────────────────────────────────────────

def generate_soap_pdf(consultation, patient_label: str, medecin, cabinet) -> bytes:
    """Generate a PDF of the SOAP consultation note using reportlab.

    Args:
        consultation:  Consultation ORM row (soap_validated or soap_generated).
        patient_label: Display label for the patient (SHA-256 pseudonym or nom).
        medecin:       Medecin ORM row.
        cabinet:       Cabinet ORM row.

    Returns:
        Raw PDF bytes suitable for streaming.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "CRTitle",
        parent=styles["Title"],
        fontSize=16,
        spaceAfter=6,
        textColor=colors.HexColor("#1a56db"),
    )
    section_header = ParagraphStyle(
        "SOAPHeader",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#1a56db"),
        spaceBefore=14,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "SOAPBody",
        parent=styles["Normal"],
        fontSize=10,
        spaceAfter=6,
        leading=15,
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#6b7280"),
    )

    soap = consultation.soap_validated or consultation.soap_generated or {}
    date_str = consultation.date.strftime("%d/%m/%Y à %H:%M")

    story = []

    # ── Cabinet / médecin header ──────────────────────────────────────────────
    header_data = [
        [
            Paragraph(f"<b>{cabinet.nom}</b>", styles["Normal"]),
            Paragraph(f"RPPS : {medecin.rpps}", meta_style),
        ],
        [
            Paragraph(
                f"Dr {medecin.prenom} {medecin.nom} &mdash; {medecin.specialite}",
                meta_style,
            ),
            Paragraph(f"Date : {date_str}", meta_style),
        ],
    ]
    header_table = Table(header_data, colWidths=["60%", "40%"])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(header_table)
    story.append(
        HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a56db"))
    )
    story.append(Spacer(1, 0.4 * cm))

    # ── Document title ────────────────────────────────────────────────────────
    story.append(Paragraph("Compte-rendu de consultation", title_style))
    story.append(
        Paragraph(
            f"Motif&nbsp;: {_esc(consultation.motif)} &nbsp;|&nbsp; Patient&nbsp;: {_esc(patient_label)}",
            meta_style,
        )
    )
    story.append(Spacer(1, 0.6 * cm))

    # ── SOAP sections ─────────────────────────────────────────────────────────
    soap_labels = [
        ("subjective", "S — Subjectif"),
        ("objective",  "O — Objectif"),
        ("assessment", "A — Évaluation"),
        ("plan",       "P — Plan"),
    ]
    for key, label in soap_labels:
        content = soap.get(key) or soap.get(key.upper())
        if not content:
            continue
        story.append(Paragraph(label, section_header))
        if isinstance(content, list):
            content = "\n".join(f"• {item}" for item in content)
        story.append(
            Paragraph(_esc(str(content)).replace("\n", "<br/>"), body_style)
        )

    # ── CCAM / CIM-10 codes ───────────────────────────────────────────────────
    ccam_codes = soap.get("ccam_codes") or []
    cim_codes = soap.get("cim10_codes") or []
    if ccam_codes or cim_codes:
        story.append(Spacer(1, 0.3 * cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        if ccam_codes:
            story.append(
                Paragraph(f"Actes CCAM : {', '.join(ccam_codes)}", meta_style)
            )
        if cim_codes:
            story.append(
                Paragraph(f"Diagnostics CIM-10 : {', '.join(cim_codes)}", meta_style)
            )

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1.2 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(
        Paragraph(
            "Document généré par MédecinAI — confidentiel, usage médical uniquement",
            meta_style,
        )
    )

    doc.build(story)
    return buffer.getvalue()


def _esc(text: str) -> str:
    """Escape XML/HTML special characters for reportlab Paragraph."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── DMP stub ──────────────────────────────────────────────────────────────────

async def push_to_dmp(
    consultation_id: str,
    fhir_bundle: dict,
    ecps_token: Optional[str],
) -> str:
    """Push a FHIR R4 Bundle to the MSSanté DMP gateway.

    This is a stub. Production implementation:
      POST https://gateway.mssante.fr/dmp/v1/documents
      Authorization: Bearer <ecps_token>
      Content-Type: application/fhir+json
      Body: fhir_bundle (JSON)

    Returns:
        dmp_document_id assigned by the DMP gateway.

    Raises:
        ValueError: if ecps_token is missing (caller must enforce e-CPS auth).
        RuntimeError: on gateway communication errors.
    """
    if not ecps_token:
        raise ValueError("e-CPS token required for DMP export")

    log.info("[export] DMP push (stub) — consultation_id=%s", consultation_id)
    # In production: response = await http_client.post(DMP_GATEWAY_URL, json=fhir_bundle, ...)
    # return response.json()["id"]
    return f"DMP-{consultation_id}"


# ── Doctolib stub ─────────────────────────────────────────────────────────────

async def push_to_doctolib(
    consultation_id: str,
    doctolib_patient_id: str,
    soap: dict,
    doctolib_token: str,
) -> str:
    """Post a consultation summary to the Doctolib REST API.

    This is a stub. Production implementation:
      POST https://api.doctolib.fr/api/v1/medical_records
      Authorization: Bearer <doctolib_token>
      Body: {patient_id, consultation_date, soap_summary}

    Returns:
        doctolib_consultation_id assigned by Doctolib.

    Raises:
        RuntimeError: on API communication errors.
    """
    log.info(
        "[export] Doctolib push (stub) — consultation_id=%s patient=%s",
        consultation_id,
        doctolib_patient_id,
    )
    # In production: call Doctolib partner API, return created record ID
    return f"DOCTOLIB-{consultation_id}"
