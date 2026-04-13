"""Business logic for doctor registration and account bootstrap."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.cabinet import Cabinet
from app.models.medecin import Medecin
from app.models.subscription import Subscription
from app.schemas.auth import MedecinResponse, RegisterRequest, TokenPayload

log = logging.getLogger(__name__)

TRIAL_DAYS = 14


async def register_medecin(
    payload: RegisterRequest,
    token: TokenPayload,
    db: AsyncSession,
    settings: Settings,
) -> MedecinResponse:
    """Create Cabinet + Medecin + Subscription (trial) atomically.

    Raises ValueError if the RPPS or auth0_sub is already registered.
    """
    # Idempotency checks
    existing_rpps = await db.execute(select(Medecin).where(Medecin.rpps == payload.rpps))
    if existing_rpps.scalar_one_or_none():
        raise ValueError(f"RPPS {payload.rpps} déjà enregistré")

    existing_sub = await db.execute(
        select(Medecin).where(Medecin.auth0_sub == token.sub)
    )
    if existing_sub.scalar_one_or_none():
        raise ValueError("Ce compte Auth0 est déjà associé à un médecin")

    # 1. Create Cabinet (rpps_titulaire left NULL for now — circular FK)
    cabinet = Cabinet(
        nom=payload.nom_cabinet,
        adresse=payload.adresse_cabinet,
        pays=payload.pays,
        siret=payload.siret,
        plan="trial",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS),
    )
    db.add(cabinet)
    await db.flush()  # get cabinet.id

    # 2. Create Medecin
    medecin = Medecin(
        cabinet_id=cabinet.id,
        rpps=payload.rpps,
        email=str(payload.email),
        nom=payload.nom,
        prenom=payload.prenom,
        specialite=payload.specialite,
        auth0_sub=token.sub,
        role="medecin",
        preferences={},
    )
    db.add(medecin)
    await db.flush()  # get medecin.id

    # 3. Resolve circular FK: cabinet.rpps_titulaire → medecin.rpps
    cabinet.rpps_titulaire = medecin.rpps

    # 4. Create Subscription (trial)
    subscription = Subscription(
        cabinet_id=cabinet.id,
        plan="trial",
        status="active",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS),
    )
    db.add(subscription)

    await db.commit()
    await db.refresh(medecin)
    await db.refresh(cabinet)

    # 5. Send confirmation email (non-blocking — failure logged, not raised)
    try:
        await _send_confirmation_email(
            to_email=str(payload.email),
            prenom=payload.prenom,
            trial_ends_at=cabinet.trial_ends_at,
            settings=settings,
        )
    except Exception as exc:
        log.error("Failed to send confirmation email to %s: %s", payload.email, exc)

    return MedecinResponse(
        medecin_id=medecin.id,
        cabinet_id=cabinet.id,
        rpps=medecin.rpps,
        email=medecin.email,
        nom=medecin.nom,
        prenom=medecin.prenom,
        specialite=medecin.specialite,
        role=medecin.role,
        trial_ends_at=cabinet.trial_ends_at.isoformat() if cabinet.trial_ends_at else None,
    )


async def _send_confirmation_email(
    to_email: str,
    prenom: str,
    trial_ends_at: datetime | None,
    settings: Settings,
) -> None:
    """Send an HTML confirmation email via aiosmtplib."""
    trial_str = trial_ends_at.strftime("%d/%m/%Y") if trial_ends_at else "14 jours"

    html = f"""
    <html><body>
    <p>Bonjour Dr {prenom},</p>
    <p>Votre compte <strong>MédecinAI</strong> a bien été créé.</p>
    <p>Votre période d'essai gratuite est active jusqu'au <strong>{trial_str}</strong>.</p>
    <p>Connectez-vous sur <a href="{settings.APP_BASE_URL}">{settings.APP_BASE_URL}</a>
    pour démarrer votre première consultation.</p>
    <p>L'équipe MédecinAI</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Bienvenue sur MédecinAI — votre compte est prêt"
    msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        start_tls=True,
    )
