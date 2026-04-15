"""Billing & subscription endpoints.

POST /billing/checkout    — create Stripe Checkout session (new subscription)
POST /billing/portal      — create Stripe Customer Portal session (manage/cancel)
POST /webhooks/stripe     — Stripe webhook receiver (invoice.paid, sub updated/deleted)
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.cabinet import Cabinet
from app.models.subscription import Subscription
from app.schemas.auth import CurrentUser
from app.security.jwt import get_current_user
from app.services.billing_service import (
    construct_webhook_event,
    create_checkout_session,
    create_portal_session,
    get_or_create_customer,
    parse_subscription_period,
    plan_from_price_id,
)

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["billing"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_cabinet(cabinet_id: UUID, db: AsyncSession) -> Cabinet:
    cab = await db.get(Cabinet, cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


async def _get_or_create_subscription(
    cabinet_id: UUID,
    db: AsyncSession,
) -> Subscription:
    result = await db.execute(
        select(Subscription).where(Subscription.cabinet_id == cabinet_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        # Bootstrap a trial subscription row if not yet present
        sub = Subscription(
            cabinet_id=cabinet_id,
            plan="trial",
            status="active",
        )
        db.add(sub)
        await db.flush()
    return sub


# ── POST /billing/checkout ────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan: str  # "solo" | "cabinet" | "reseau"
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    checkout_url: str


@router.post(
    "/billing/checkout",
    response_model=CheckoutResponse,
    summary="Create a Stripe Checkout session",
    status_code=status.HTTP_200_OK,
)
async def create_checkout(
    body: CheckoutRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CheckoutResponse:
    """Create (or renew) a Stripe Checkout session for the given plan.

    If the cabinet does not yet have a Stripe customer, one is created and
    ``stripe_customer_id`` is persisted on the Cabinet row.
    Returns the Checkout session URL to which the frontend must redirect the user.
    """
    if body.plan not in ("solo", "cabinet", "reseau"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Plan invalide. Valeurs : solo, cabinet, reseau",
        )

    cabinet = await _get_cabinet(current_user.cabinet_id, db)

    try:
        new_customer_id = get_or_create_customer(
            cabinet_id=str(cabinet.id),
            cabinet_nom=cabinet.nom,
            stripe_customer_id=cabinet.stripe_customer_id,
        )
    except stripe.error.StripeError as exc:
        log.error("[billing] Stripe customer error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message}")

    # Persist stripe_customer_id if newly created
    if cabinet.stripe_customer_id != new_customer_id:
        cabinet.stripe_customer_id = new_customer_id
        await db.commit()

    try:
        checkout_url = create_checkout_session(
            stripe_customer_id=new_customer_id,
            plan=body.plan,
            cabinet_id=str(current_user.cabinet_id),
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except stripe.error.StripeError as exc:
        log.error("[billing] Checkout session error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message}")

    return CheckoutResponse(checkout_url=checkout_url)


# ── POST /billing/portal ──────────────────────────────────────────────────────

class PortalRequest(BaseModel):
    return_url: str


class PortalResponse(BaseModel):
    portal_url: str


@router.post(
    "/billing/portal",
    response_model=PortalResponse,
    summary="Create a Stripe Customer Portal session",
    status_code=status.HTTP_200_OK,
)
async def create_portal(
    body: PortalRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> PortalResponse:
    """Create a Stripe Customer Portal session for managing or cancelling the subscription.

    The cabinet must have completed at least one Checkout session first
    (i.e. ``stripe_customer_id`` must be set). Returns 422 otherwise.
    """
    cabinet = await _get_cabinet(current_user.cabinet_id, db)

    if not cabinet.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Aucun abonnement actif. Veuillez d'abord compléter le paiement.",
        )

    try:
        portal_url = create_portal_session(
            stripe_customer_id=cabinet.stripe_customer_id,
            return_url=body.return_url,
        )
    except stripe.error.StripeError as exc:
        log.error("[billing] Portal session error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message}")

    return PortalResponse(portal_url=portal_url)


# ── POST /webhooks/stripe ─────────────────────────────────────────────────────

@router.post(
    "/webhooks/stripe",
    summary="Stripe webhook receiver",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,  # Not shown in public docs
)
async def stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    stripe_signature: Annotated[Optional[str], Header(alias="stripe-signature")] = None,
) -> JSONResponse:
    """Receive and process Stripe webhook events.

    Verifies the ``Stripe-Signature`` header. Handles:
    - ``invoice.paid``                   — subscription payment confirmed
    - ``customer.subscription.updated``  — plan / status / period changed
    - ``customer.subscription.deleted``  — subscription cancelled

    Always returns 200 to Stripe (even on business errors) to avoid retries
    for events we intentionally ignore. Returns 400 only on signature failure.
    """
    payload = await request.body()

    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        event = construct_webhook_event(payload, stripe_signature)
    except stripe.error.SignatureVerificationError:
        log.warning("[billing] Webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    event_type = event["type"]
    log.info("[billing] Webhook event: %s id=%s", event_type, event["id"])

    if event_type == "invoice.paid":
        await _handle_invoice_paid(event["data"]["object"], db)

    elif event_type == "customer.subscription.updated":
        await _handle_subscription_updated(event["data"]["object"], db)

    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(event["data"]["object"], db)

    else:
        log.debug("[billing] Unhandled webhook event type: %s", event_type)

    return JSONResponse(content={"received": True})


# ── Webhook handlers ──────────────────────────────────────────────────────────

async def _resolve_cabinet_id(stripe_object: dict, db: AsyncSession) -> Optional[UUID]:
    """Extract cabinet_id from Stripe object metadata or customer lookup."""
    # Prefer metadata set during checkout
    cabinet_id_str = (stripe_object.get("metadata") or {}).get("cabinet_id")
    if cabinet_id_str:
        try:
            return UUID(cabinet_id_str)
        except ValueError:
            pass

    # Fallback: look up cabinet by stripe_customer_id
    customer_id = stripe_object.get("customer")
    if customer_id:
        result = await db.execute(
            select(Cabinet).where(Cabinet.stripe_customer_id == customer_id)
        )
        cab = result.scalar_one_or_none()
        if cab:
            return cab.id

    log.warning("[billing] Could not resolve cabinet_id from stripe object: %s", stripe_object.get("id"))
    return None


async def _get_subscription_row(cabinet_id: UUID, db: AsyncSession) -> Optional[Subscription]:
    result = await db.execute(
        select(Subscription).where(Subscription.cabinet_id == cabinet_id)
    )
    return result.scalar_one_or_none()


async def _handle_invoice_paid(invoice: dict, db: AsyncSession) -> None:
    """invoice.paid — payment confirmed, activate subscription."""
    cabinet_id = await _resolve_cabinet_id(invoice, db)
    if cabinet_id is None:
        return

    # invoice.paid carries the subscription ID
    stripe_sub_id = invoice.get("subscription")
    if not stripe_sub_id:
        return

    sub = await _get_subscription_row(cabinet_id, db)
    if sub is None:
        log.warning("[billing] No Subscription row for cabinet %s on invoice.paid", cabinet_id)
        return

    # Fetch the subscription object to get period dates and plan
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
    except stripe.error.StripeError as exc:
        log.error("[billing] Could not retrieve subscription %s: %s", stripe_sub_id, exc)
        return

    price_id = stripe_sub["items"]["data"][0]["price"]["id"] if stripe_sub["items"]["data"] else None
    period_start, period_end = parse_subscription_period(dict(stripe_sub))

    sub.stripe_subscription_id = stripe_sub_id
    sub.status = "active"
    sub.plan = plan_from_price_id(price_id) if price_id else sub.plan
    sub.current_period_start = period_start
    sub.current_period_end = period_end
    sub.cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end", False))

    await db.commit()
    log.info(
        "[billing] invoice.paid — cabinet=%s sub=%s plan=%s period_end=%s",
        cabinet_id, stripe_sub_id, sub.plan, period_end,
    )


async def _handle_subscription_updated(stripe_sub: dict, db: AsyncSession) -> None:
    """customer.subscription.updated — sync plan / status / period."""
    cabinet_id = await _resolve_cabinet_id(stripe_sub, db)
    if cabinet_id is None:
        return

    sub = await _get_subscription_row(cabinet_id, db)
    if sub is None:
        return

    price_id = stripe_sub.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
    period_start, period_end = parse_subscription_period(stripe_sub)

    stripe_status = stripe_sub.get("status", "active")
    # Map Stripe statuses to our enum
    status_map = {
        "active":   "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid":   "unpaid",
        "trialing": "active",   # treat trialing as active
        "incomplete": "unpaid",
        "incomplete_expired": "canceled",
        "paused": "past_due",
    }
    sub.status = status_map.get(stripe_status, "past_due")
    if price_id:
        sub.plan = plan_from_price_id(price_id)
    sub.stripe_subscription_id = stripe_sub.get("id", sub.stripe_subscription_id)
    sub.current_period_start = period_start
    sub.current_period_end = period_end
    sub.cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end", False))

    await db.commit()
    log.info(
        "[billing] subscription.updated — cabinet=%s status=%s plan=%s",
        cabinet_id, sub.status, sub.plan,
    )


async def _handle_subscription_deleted(stripe_sub: dict, db: AsyncSession) -> None:
    """customer.subscription.deleted — mark subscription as canceled."""
    cabinet_id = await _resolve_cabinet_id(stripe_sub, db)
    if cabinet_id is None:
        return

    sub = await _get_subscription_row(cabinet_id, db)
    if sub is None:
        return

    sub.status = "canceled"
    sub.cancel_at_period_end = False
    await db.commit()
    log.info("[billing] subscription.deleted — cabinet=%s", cabinet_id)
