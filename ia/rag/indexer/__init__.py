"""RAG indexers — one per knowledge-base namespace.

Namespaces:
    NS1 — ccam          → CcamIndexer
    NS2 — has           → HasIndexer
    NS3 — vidal         → VidalIndexer
    NS4 — patient_history → PatientIndexer  (async, mandatory encryption)
    NS5 — doctor_corpus → DoctorStyleIndexer (async, quality-gated)
"""
