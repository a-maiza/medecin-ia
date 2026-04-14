"""Drug interaction checker service.

Deterministic SQL lookup on `drug_interaction` table.
Must execute in < 5 ms (indexed pair lookup, Redis cache).

Severity mapping (DB enum → public API):
    contre_indication      → CI_ABSOLUE   (blocks SOAP entirely)
    association_deconseille → CI_RELATIVE  (requires clinical justification)
    precaution_emploi       → PRECAUTION   (SOAP generated with mention in plan)
    a_prendre_en_compte     → INFO         (informational only)

Usage:
    checker = get_interaction_checker()
    result = await checker.check(new_drugs=["metformine"], active_drugs=["ibuprofen"], db=db, redis=redis)
    if result.has_ci_absolue:
        # block SOAP
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# ── Severity ordering (highest → lowest) ─────────────────────────────────────

_SEVERITY_ORDER = {
    "contre_indication": 0,
    "association_deconseille": 1,
    "precaution_emploi": 2,
    "a_prendre_en_compte": 3,
}

_SEVERITY_TO_PUBLIC = {
    "contre_indication": "CI_ABSOLUE",
    "association_deconseille": "CI_RELATIVE",
    "precaution_emploi": "PRECAUTION",
    "a_prendre_en_compte": "INFO",
}

# ── DCI normalisation table (commercial name → DCI lowercase) ─────────────────
# 200+ entries covering the most prescribed drugs in France (BDPM / Vidal).

_COMMERCIAL_TO_DCI: dict[str, str] = {
    # Antibiotiques
    "augmentin": "amoxicilline acide clavulanique",
    "amoxicilline": "amoxicilline",
    "clamoxyl": "amoxicilline",
    "orelox": "cefpodoxime",
    "cefpodoxime": "cefpodoxime",
    "rocephine": "ceftriaxone",
    "ceftriaxone": "ceftriaxone",
    "ciflox": "ciprofloxacine",
    "ciprofloxacine": "ciprofloxacine",
    "oflocet": "ofloxacine",
    "ofloxacine": "ofloxacine",
    "zithromax": "azithromycine",
    "azithromycine": "azithromycine",
    "josacine": "josamycine",
    "doxycycline": "doxycycline",
    "vibramycine": "doxycycline",
    "metronidazole": "metronidazole",
    "flagyl": "metronidazole",
    "bactrim": "sulfamethoxazole trimethoprime",
    "trimethoprime": "trimethoprime",
    "clindamycine": "clindamycine",
    "dalacine": "clindamycine",
    # AINS
    "ibuprofen": "ibuprofène",
    "ibuprofène": "ibuprofène",
    "advil": "ibuprofène",
    "nurofen": "ibuprofène",
    "voltarene": "diclofenac",
    "diclofenac": "diclofenac",
    "ketoprofene": "kétoprofène",
    "kétoprofène": "kétoprofène",
    "profenid": "kétoprofène",
    "naproxene": "naproxène",
    "naproxène": "naproxène",
    "apranax": "naproxène",
    "piroxicam": "piroxicam",
    "feldene": "piroxicam",
    "celecoxib": "célécoxib",
    "celebrex": "célécoxib",
    # Antalgiques
    "doliprane": "paracétamol",
    "paracetamol": "paracétamol",
    "paracétamol": "paracétamol",
    "efferalgan": "paracétamol",
    "dafalgan": "paracétamol",
    "codeine": "codéine",
    "codéine": "codéine",
    "tramadol": "tramadol",
    "topalgic": "tramadol",
    "contramal": "tramadol",
    "morphine": "morphine",
    "sevredol": "morphine",
    "oxycodone": "oxycodone",
    "oxycontin": "oxycodone",
    "fentanyl": "fentanyl",
    "durogesic": "fentanyl",
    "buprenorphine": "buprénorphine",
    "buprénorphine": "buprénorphine",
    "temgesic": "buprénorphine",
    # Anticoagulants
    "warfarine": "warfarine",
    "coumadine": "warfarine",
    "acenocoumarol": "acénocoumarol",
    "acénocoumarol": "acénocoumarol",
    "sintrom": "acénocoumarol",
    "heparine": "héparine",
    "héparine": "héparine",
    "lovenox": "enoxaparine",
    "enoxaparine": "enoxaparine",
    "innohep": "tinzaparine",
    "xarelto": "rivaroxaban",
    "rivaroxaban": "rivaroxaban",
    "eliquis": "apixaban",
    "apixaban": "apixaban",
    "pradaxa": "dabigatran",
    "dabigatran": "dabigatran",
    # Antiagrégants plaquettaires
    "aspirine": "acide acétylsalicylique",
    "acide acetylsalicylique": "acide acétylsalicylique",
    "acide acétylsalicylique": "acide acétylsalicylique",
    "kardegic": "acide acétylsalicylique",
    "aspegic": "acide acétylsalicylique",
    "plavix": "clopidogrel",
    "clopidogrel": "clopidogrel",
    "brilique": "ticagrelor",
    "ticagrelor": "ticagrelor",
    "efient": "prasugrel",
    "prasugrel": "prasugrel",
    # Antihypertenseurs - IEC
    "ramipril": "ramipril",
    "triatec": "ramipril",
    "perindopril": "périndopril",
    "périndopril": "périndopril",
    "coversyl": "périndopril",
    "lisinopril": "lisinopril",
    "zestril": "lisinopril",
    "enalapril": "énalapril",
    "énalapril": "énalapril",
    "renitec": "énalapril",
    # Antihypertenseurs - ARAII
    "valsartan": "valsartan",
    "nisis": "valsartan",
    "losartan": "losartan",
    "cozaar": "losartan",
    "irbesartan": "irbésartan",
    "irbésartan": "irbésartan",
    "aprovel": "irbésartan",
    "olmesartan": "olmésartan",
    "olmésartan": "olmésartan",
    "olmetec": "olmésartan",
    # Bêtabloquants
    "bisoprolol": "bisoprolol",
    "cardensiel": "bisoprolol",
    "concor": "bisoprolol",
    "metoprolol": "métoprolol",
    "métoprolol": "métoprolol",
    "seloken": "métoprolol",
    "atenolol": "aténolol",
    "aténolol": "aténolol",
    "tenormine": "aténolol",
    "nebivolol": "nébivolol",
    "nébivolol": "nébivolol",
    "temerit": "nébivolol",
    "propranolol": "propranolol",
    "avlocardyl": "propranolol",
    # Diurétiques
    "furosemide": "furosémide",
    "furosémide": "furosémide",
    "lasilix": "furosémide",
    "hydrochlorothiazide": "hydrochlorothiazide",
    "spironolactone": "spironolactone",
    "aldactone": "spironolactone",
    "eplerenone": "éplérénone",
    "éplérénone": "éplérénone",
    "inspra": "éplérénone",
    "indapamide": "indapamide",
    "fludex": "indapamide",
    # Antidiabétiques
    "metformine": "metformine",
    "glucophage": "metformine",
    "glucinan": "metformine",
    "glibenclamide": "glibenclamide",
    "daonil": "glibenclamide",
    "gliclazide": "gliclazide",
    "diamicron": "gliclazide",
    "glimepiride": "glimépiride",
    "glimépiride": "glimépiride",
    "amaryl": "glimépiride",
    "sitagliptine": "sitagliptine",
    "januvia": "sitagliptine",
    "saxagliptine": "saxagliptine",
    "vildagliptine": "vildagliptine",
    "galvus": "vildagliptine",
    "empagliflozine": "empagliflozine",
    "jardiance": "empagliflozine",
    "dapagliflozine": "dapagliflozine",
    "forxiga": "dapagliflozine",
    "liraglutide": "liraglutide",
    "victoza": "liraglutide",
    "semaglutide": "sémaglutide",
    "sémaglutide": "sémaglutide",
    "ozempic": "sémaglutide",
    "insuline": "insuline",
    # Statines
    "atorvastatine": "atorvastatine",
    "tahor": "atorvastatine",
    "rosuvastatine": "rosuvastatine",
    "crestor": "rosuvastatine",
    "simvastatine": "simvastatine",
    "zocor": "simvastatine",
    "pravastatine": "pravastatine",
    "elisor": "pravastatine",
    "fluvastatine": "fluvastatine",
    "lescol": "fluvastatine",
    # Psychotropes - antidépresseurs
    "sertraline": "sertraline",
    "zoloft": "sertraline",
    "escitalopram": "escitalopram",
    "seroplex": "escitalopram",
    "paroxetine": "paroxétine",
    "paroxétine": "paroxétine",
    "deroxat": "paroxétine",
    "fluoxetine": "fluoxétine",
    "fluoxétine": "fluoxétine",
    "prozac": "fluoxétine",
    "venlafaxine": "venlafaxine",
    "effexor": "venlafaxine",
    "duloxetine": "duloxétine",
    "duloxétine": "duloxétine",
    "cymbalta": "duloxétine",
    "mirtazapine": "mirtazapine",
    "norset": "mirtazapine",
    "amitriptyline": "amitriptyline",
    "laroxyl": "amitriptyline",
    "clomipramine": "clomipramine",
    "anafranil": "clomipramine",
    "lithium": "lithium",
    "teralithe": "lithium",
    # Psychotropes - antipsychotiques
    "olanzapine": "olanzapine",
    "zyprexa": "olanzapine",
    "risperidone": "rispéridone",
    "rispéridone": "rispéridone",
    "risperdal": "rispéridone",
    "quetiapine": "quétiapine",
    "quétiapine": "quétiapine",
    "xeroquel": "quétiapine",
    "aripiprazole": "aripiprazole",
    "abilify": "aripiprazole",
    "haloperidol": "halopéridol",
    "halopéridol": "halopéridol",
    "haldol": "halopéridol",
    "clozapine": "clozapine",
    "leponex": "clozapine",
    # Benzodiazépines / anxiolytiques
    "alprazolam": "alprazolam",
    "xanax": "alprazolam",
    "diazepam": "diazépam",
    "diazépam": "diazépam",
    "valium": "diazépam",
    "lorazepam": "lorazépam",
    "lorazépam": "lorazépam",
    "temesta": "lorazépam",
    "clonazepam": "clonazépam",
    "clonazépam": "clonazépam",
    "rivotril": "clonazépam",
    "bromazepam": "bromazépam",
    "bromazépam": "bromazépam",
    "lexomil": "bromazépam",
    "zolpidem": "zolpidem",
    "stilnox": "zolpidem",
    "zopiclone": "zopiclone",
    "imovane": "zopiclone",
    # IPP et gastriques
    "omeprazole": "oméprazole",
    "oméprazole": "oméprazole",
    "mopral": "oméprazole",
    "pantoprazole": "pantoprazole",
    "inipomp": "pantoprazole",
    "esomeprazole": "ésoméprazole",
    "ésoméprazole": "ésoméprazole",
    "inexium": "ésoméprazole",
    "lansoprazole": "lansoprazole",
    "lanzor": "lansoprazole",
    # Corticoïdes
    "prednisolone": "prednisolone",
    "solupred": "prednisolone",
    "prednisone": "prednisone",
    "cortancyl": "prednisone",
    "methylprednisolone": "méthylprednisolone",
    "méthylprednisolone": "méthylprednisolone",
    "medrol": "méthylprednisolone",
    "dexamethasone": "dexaméthasone",
    "dexaméthasone": "dexaméthasone",
    "hydrocortisone": "hydrocortisone",
    "betamethasone": "bétaméthasone",
    "bétaméthasone": "bétaméthasone",
    "celestene": "bétaméthasone",
    # Thyroïde
    "levothyroxine": "lévothyroxine",
    "lévothyroxine": "lévothyroxine",
    "levothyrox": "lévothyroxine",
    "euthyrox": "lévothyroxine",
    # Antiépileptiques
    "valproate": "valproate de sodium",
    "depakine": "valproate de sodium",
    "valproate de sodium": "valproate de sodium",
    "lamotrigine": "lamotrigine",
    "lamictal": "lamotrigine",
    "levetiracetam": "lévétiracétam",
    "lévétiracétam": "lévétiracétam",
    "keppra": "lévétiracétam",
    "carbamazepine": "carbamazépine",
    "carbamazépine": "carbamazépine",
    "tegretol": "carbamazépine",
    "gabapentine": "gabapentine",
    "neurontin": "gabapentine",
    "pregabaline": "prégabaline",
    "prégabaline": "prégabaline",
    "lyrica": "prégabaline",
    "phenytoine": "phénytoïne",
    "phénytoïne": "phénytoïne",
    "dihydan": "phénytoïne",
    # Antiviraux
    "aciclovir": "aciclovir",
    "zovirax": "aciclovir",
    "valaciclovir": "valaciclovir",
    "zelitrex": "valaciclovir",
    "tenofovir": "ténofovir",
    "ténofovir": "ténofovir",
    "viread": "ténofovir",
    # Antiparasitaires
    "ivermectine": "ivermectine",
    "stromectol": "ivermectine",
    "albendazole": "albendazole",
    "zentel": "albendazole",
    # Divers
    "colchicine": "colchicine",
    "colchicine opocalcium": "colchicine",
    "allopurinol": "allopurinol",
    "zyloric": "allopurinol",
    "febuxostat": "febuxostat",
    "adenuric": "febuxostat",
    "digoxine": "digoxine",
    "hemigoxine": "digoxine",
    "amiodarone": "amiodarone",
    "cordarone": "amiodarone",
    "flecainide": "flécaïnide",
    "flécaïnide": "flécaïnide",
    "flecaine": "flécaïnide",
    "nifedipine": "nifédipine",
    "nifédipine": "nifédipine",
    "adalate": "nifédipine",
    "amlodipine": "amlodipine",
    "amlor": "amlodipine",
    "lercanidipine": "lercanidipine",
    "zanidip": "lercanidipine",
    "diltiazem": "diltiazem",
    "tildiem": "diltiazem",
    "verapamil": "vérapamil",
    "vérapamil": "vérapamil",
    "isoptine": "vérapamil",
    "dexametasone": "dexaméthasone",
    "clonidine": "clonidine",
    "catapressan": "clonidine",
    "moxonidine": "moxonidine",
    "physiotens": "moxonidine",
    "doxazosine": "doxazosine",
    "zoxan": "doxazosine",
    "tamsulosine": "tamsulosine",
    "josir": "tamsulosine",
    "omix": "tamsulosine",
    "alfuzosine": "alfuzosine",
    "xatral": "alfuzosine",
    "finasteride": "finastéride",
    "finastéride": "finastéride",
    "chibro-proscar": "finastéride",
    "dutasteride": "dutastéride",
    "dutastéride": "dutastéride",
    "avodart": "dutastéride",
    "ivabradine": "ivabradine",
    "procoralan": "ivabradine",
    "ezetimibe": "ézétimibe",
    "ézétimibe": "ézétimibe",
    "ezetrol": "ézétimibe",
    "olmesartan medoxomil": "olmésartan",
    "telmisartan": "telmisartan",
    "micardis": "telmisartan",
    "candesartan": "candésartan",
    "candésartan": "candésartan",
    "atacand": "candésartan",
    "potassium": "potassium",
    "diffu-k": "potassium",
    "kaleorid": "potassium",
    "calcium": "calcium",
    "vitamine d": "cholécalciférol",
    "cholecalciferol": "cholécalciférol",
    "cholécalciférol": "cholécalciférol",
    "uvedose": "cholécalciférol",
    "zyma d": "cholécalciférol",
    "vitamine k": "phytoménadione",
    "phytomenadione": "phytoménadione",
    "phytoménadione": "phytoménadione",
    "vitamine k1": "phytoménadione",
    "konakion": "phytoménadione",
    "fer": "fer",
    "ferrostrane": "fer",
    "tiorfan": "racécadotril",
    "racecadotril": "racécadotril",
    "racécadotril": "racécadotril",
    "smecta": "diosmectite",
    "loperamide": "lopéramide",
    "lopéramide": "lopéramide",
    "imodium": "lopéramide",
    "domperidone": "dompéridone",
    "dompéridone": "dompéridone",
    "motilium": "dompéridone",
    "metoclopramide": "métoclopramide",
    "métoclopramide": "métoclopramide",
    "primperan": "métoclopramide",
    "ondansetron": "ondansétron",
    "ondansétron": "ondansétron",
    "zophren": "ondansétron",
    "antihistaminique": "antihistaminique",
    "cetirizine": "cétirizine",
    "cétirizine": "cétirizine",
    "zyrtec": "cétirizine",
    "loratadine": "loratadine",
    "clarityne": "loratadine",
    "desloratadine": "desloratadine",
    "aerius": "desloratadine",
    "fexofenadine": "fexofénadine",
    "fexofénadine": "fexofénadine",
    "telfast": "fexofénadine",
    "montelukast": "montélukast",
    "montélukast": "montélukast",
    "singulair": "montélukast",
    "salbutamol": "salbutamol",
    "ventoline": "salbutamol",
    "formoterol": "formotérol",
    "formotérol": "formotérol",
    "foradil": "formotérol",
    "salmeterol": "salmétérol",
    "salmétérol": "salmétérol",
    "serevent": "salmétérol",
    "tiotropium": "tiotropium",
    "spiriva": "tiotropium",
    "fluticasone": "fluticasone",
    "flixotide": "fluticasone",
    "budesonide": "budésonide",
    "budésonide": "budésonide",
    "pulmicort": "budésonide",
    "beclometasone": "béclométasone",
    "béclométasone": "béclométasone",
    "beclomethasone": "béclométasone",
}


def normalise_dci(drug_name: str) -> str:
    """Normalise a drug name (commercial or DCI) to lowercase DCI.

    Falls back to the lowercased input if not found in the mapping.
    Strips common French suffixes like dosage forms, strength units.
    """
    if not drug_name:
        return ""

    # Strip whitespace, lowercase
    cleaned = drug_name.strip().lower()

    # Strip common dosage forms and strengths (e.g. " 500mg", " 1000 ui/ml")
    import re
    cleaned = re.sub(r"\s+\d+[\s,.]?\d*\s*(mg|mcg|μg|g|ml|l|ui|iu|mmol|%|cp|gél|comp|mg/ml|mg/j).*$", "", cleaned)
    cleaned = cleaned.strip()

    return _COMMERCIAL_TO_DCI.get(cleaned, cleaned)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class InteractionAlert:
    drug_a: str               # normalised DCI
    drug_b: str               # normalised DCI
    severity: str             # CI_ABSOLUE | CI_RELATIVE | PRECAUTION | INFO
    severity_raw: str         # raw DB enum value
    description: str
    source: str

    @property
    def is_ci_absolue(self) -> bool:
        return self.severity == "CI_ABSOLUE"

    @property
    def is_ci_relative(self) -> bool:
        return self.severity == "CI_RELATIVE"

    def to_dict(self) -> dict:
        return {
            "drug_a": self.drug_a,
            "drug_b": self.drug_b,
            "severity": self.severity,
            "description": self.description,
            "source": self.source,
        }


@dataclass
class InteractionCheckResult:
    alerts: list[InteractionAlert] = field(default_factory=list)
    checked_drugs: list[str] = field(default_factory=list)   # normalised DCIs checked
    from_cache: bool = False

    @property
    def has_ci_absolue(self) -> bool:
        return any(a.is_ci_absolue for a in self.alerts)

    @property
    def has_ci_relative(self) -> bool:
        return any(a.is_ci_relative for a in self.alerts)

    @property
    def highest_severity(self) -> Optional[str]:
        if not self.alerts:
            return None
        return self.alerts[0].severity  # already sorted by severity descending


# ── Cache helpers ─────────────────────────────────────────────────────────────

_CACHE_TTL = 3600  # 1 hour — interactions don't change intraday


def _cache_key(drug_names: list[str]) -> str:
    """Deterministic cache key: sorted normalised DCIs, SHA-256 prefix."""
    key_str = "|".join(sorted(drug_names))
    h = hashlib.sha256(key_str.encode()).hexdigest()[:16]
    return f"interactions:{h}"


# ── Core service ──────────────────────────────────────────────────────────────

class InteractionChecker:
    """Stateless interaction checker.

    Thread-safe and async-safe — share a single instance per application.
    """

    async def check(
        self,
        new_drugs: list[str],
        active_drugs: list[str],
        db: AsyncSession,
        redis=None,             # Optional aioredis.Redis — enables caching
    ) -> InteractionCheckResult:
        """Check interactions between new_drugs and active_drugs.

        Normalises all names to DCI, queries all unique pairs, returns results
        sorted by severity (CI_ABSOLUE first).

        Must be < 5 ms (indexed SQL, Redis cache).
        """
        all_drugs = [normalise_dci(d) for d in (new_drugs + active_drugs) if d.strip()]
        # Deduplicate
        seen: set[str] = set()
        unique_drugs: list[str] = []
        for d in all_drugs:
            if d not in seen:
                seen.add(d)
                unique_drugs.append(d)

        if len(unique_drugs) < 2:
            return InteractionCheckResult(checked_drugs=unique_drugs)

        # ── Try Redis cache ────────────────────────────────────────────────────
        cache_key = _cache_key(unique_drugs)
        if redis is not None:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    alerts = [InteractionAlert(**a) for a in data]
                    return InteractionCheckResult(
                        alerts=alerts,
                        checked_drugs=unique_drugs,
                        from_cache=True,
                    )
            except Exception as exc:
                log.warning("[interaction_checker] Redis cache read failed: %s", exc)

        # ── SQL lookup ────────────────────────────────────────────────────────
        alerts = await self._sql_lookup(unique_drugs, db)

        # ── Write to Redis cache ───────────────────────────────────────────────
        if redis is not None:
            try:
                payload = json.dumps([
                    {
                        "drug_a": a.drug_a,
                        "drug_b": a.drug_b,
                        "severity": a.severity,
                        "severity_raw": a.severity_raw,
                        "description": a.description,
                        "source": a.source,
                    }
                    for a in alerts
                ])
                await redis.setex(cache_key, _CACHE_TTL, payload)
            except Exception as exc:
                log.warning("[interaction_checker] Redis cache write failed: %s", exc)

        return InteractionCheckResult(alerts=alerts, checked_drugs=unique_drugs)

    async def _sql_lookup(
        self,
        drug_names: list[str],
        db: AsyncSession,
    ) -> list[InteractionAlert]:
        """Build all unique canonical pairs and execute a single indexed query."""
        from app.models.drug_interaction import DrugInteraction

        pairs: list[tuple[str, str]] = []
        n = len(drug_names)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = sorted([drug_names[i], drug_names[j]])
                pairs.append((a, b))

        if not pairs:
            return []

        conditions = [
            and_(DrugInteraction.drug_a == a, DrugInteraction.drug_b == b)
            for a, b in pairs
        ]
        stmt = (
            select(DrugInteraction)
            .where(or_(*conditions))
        )
        result = await db.execute(stmt)
        rows = list(result.scalars().all())

        alerts = [
            InteractionAlert(
                drug_a=row.drug_a,
                drug_b=row.drug_b,
                severity=_SEVERITY_TO_PUBLIC.get(row.severity, row.severity),
                severity_raw=row.severity,
                description=row.description,
                source=row.source,
            )
            for row in rows
        ]

        # Sort: CI_ABSOLUE first, then CI_RELATIVE, then PRECAUTION, then INFO
        alerts.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity_raw, 99))
        return alerts

    def check_dfg_alerts(
        self,
        drug_names: list[str],
        dfg: Optional[float],
    ) -> list["DFGAlert"]:
        """Check renal dosage restrictions for a list of drugs.

        Uses a static table of known renal-risk drugs with VIDAL thresholds.
        Returns CRITIQUE alerts (DFG < absolute_min) or ATTENTION alerts
        (DFG < caution_threshold).

        Called during SOAP prompt assembly — no DB round-trip required.
        """
        if dfg is None:
            return []

        alerts: list[DFGAlert] = []
        for drug_name in drug_names:
            dci = normalise_dci(drug_name)
            rule = _DFG_RULES.get(dci)
            if rule is None:
                continue
            if dfg < rule["absolute_min"]:
                alerts.append(DFGAlert(
                    drug=dci,
                    dfg=dfg,
                    threshold=rule["absolute_min"],
                    severity="CRITIQUE",
                    message=(
                        f"{dci.capitalize()} contre-indiqué si DFG < "
                        f"{rule['absolute_min']} mL/min/1,73m² "
                        f"(DFG patient : {dfg:.0f})"
                    ),
                ))
            elif dfg < rule["caution_threshold"]:
                alerts.append(DFGAlert(
                    drug=dci,
                    dfg=dfg,
                    threshold=rule["caution_threshold"],
                    severity="ATTENTION",
                    message=(
                        f"{dci.capitalize()} : adaptation posologique "
                        f"recommandée si DFG < {rule['caution_threshold']} "
                        f"mL/min/1,73m² (DFG patient : {dfg:.0f})"
                    ),
                ))
        # CRITIQUE first
        alerts.sort(key=lambda a: 0 if a.severity == "CRITIQUE" else 1)
        return alerts


# ── DFG (renal dosage) rules ─────────────────────────────────────────────────
# Key: normalised DCI, value: {absolute_min, caution_threshold} mL/min/1.73m²
# Source: Vidal / BDPM résumés des caractéristiques du produit (RCP).

_DFG_RULES: dict[str, dict[str, float]] = {
    "metformine":             {"absolute_min": 30.0, "caution_threshold": 45.0},
    "ibuprofène":             {"absolute_min": 30.0, "caution_threshold": 60.0},
    "diclofenac":             {"absolute_min": 30.0, "caution_threshold": 60.0},
    "kétoprofène":            {"absolute_min": 30.0, "caution_threshold": 60.0},
    "naproxène":              {"absolute_min": 30.0, "caution_threshold": 60.0},
    "célécoxib":              {"absolute_min": 30.0, "caution_threshold": 60.0},
    "piroxicam":              {"absolute_min": 30.0, "caution_threshold": 60.0},
    "acide acétylsalicylique": {"absolute_min": 10.0, "caution_threshold": 30.0},
    "enoxaparine":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "rivaroxaban":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "apixaban":               {"absolute_min": 15.0, "caution_threshold": 25.0},
    "dabigatran":             {"absolute_min": 30.0, "caution_threshold": 50.0},
    "digoxine":               {"absolute_min": 20.0, "caution_threshold": 50.0},
    "allopurinol":            {"absolute_min": 10.0, "caution_threshold": 30.0},
    "colchicine":             {"absolute_min": 10.0, "caution_threshold": 30.0},
    "furosémide":             {"absolute_min": 15.0, "caution_threshold": 30.0},
    "spironolactone":         {"absolute_min": 30.0, "caution_threshold": 45.0},
    "éplérénone":             {"absolute_min": 30.0, "caution_threshold": 50.0},
    "sitagliptine":           {"absolute_min": 15.0, "caution_threshold": 45.0},
    "empagliflozine":         {"absolute_min": 20.0, "caution_threshold": 45.0},
    "dapagliflozine":         {"absolute_min": 25.0, "caution_threshold": 45.0},
    "ténofovir":              {"absolute_min": 30.0, "caution_threshold": 60.0},
    "lithium":                {"absolute_min": 20.0, "caution_threshold": 50.0},
    "valproate de sodium":    {"absolute_min": 20.0, "caution_threshold": 50.0},
    "gabapentine":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "prégabaline":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "tramadol":               {"absolute_min": 10.0, "caution_threshold": 30.0},
    "codéine":                {"absolute_min": 10.0, "caution_threshold": 30.0},
    "morphine":               {"absolute_min": 10.0, "caution_threshold": 30.0},
    "oxycodone":              {"absolute_min": 10.0, "caution_threshold": 30.0},
    "olmésartan":             {"absolute_min": 20.0, "caution_threshold": 30.0},
    "ramipril":               {"absolute_min": 10.0, "caution_threshold": 30.0},
    "lisinopril":             {"absolute_min": 10.0, "caution_threshold": 30.0},
    "énalapril":              {"absolute_min": 10.0, "caution_threshold": 30.0},
    "périndopril":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "valsartan":              {"absolute_min": 10.0, "caution_threshold": 30.0},
    "losartan":               {"absolute_min": 10.0, "caution_threshold": 30.0},
    "irbésartan":             {"absolute_min": 30.0, "caution_threshold": 60.0},
    "candésartan":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "telmisartan":            {"absolute_min": 30.0, "caution_threshold": 60.0},
    "hydrochlorothiazide":    {"absolute_min": 30.0, "caution_threshold": 45.0},
    "indapamide":             {"absolute_min": 25.0, "caution_threshold": 45.0},
    "febuxostat":             {"absolute_min": 15.0, "caution_threshold": 30.0},
    "clopidogrel":            {"absolute_min": 15.0, "caution_threshold": 30.0},
    "ticagrelor":             {"absolute_min": 15.0, "caution_threshold": 30.0},
    "amiodarone":             {"absolute_min": 15.0, "caution_threshold": 30.0},
    "metronidazole":          {"absolute_min": 10.0, "caution_threshold": 30.0},
    "ciprofloxacine":         {"absolute_min": 15.0, "caution_threshold": 30.0},
}


@dataclass
class DFGAlert:
    """Renal dosage restriction alert for a single drug."""

    drug: str           # normalised DCI
    dfg: float          # patient's measured DFG (mL/min/1.73m²)
    threshold: float    # VIDAL threshold that was breached
    severity: str       # CRITIQUE | ATTENTION
    message: str        # human-readable French message


# ── Module-level singleton ────────────────────────────────────────────────────

_checker: Optional[InteractionChecker] = None


def get_interaction_checker() -> InteractionChecker:
    """Return the application-wide InteractionChecker singleton."""
    global _checker
    if _checker is None:
        _checker = InteractionChecker()
    return _checker
