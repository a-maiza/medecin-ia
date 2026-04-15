"""Stripe billing service — pure API wrappers, no database access.

Database updates (persisting stripe_customer_id, updating Subscription rows)
are performed by the router using the results returned here.

Plans and their Stripe Price IDs are read from Settings:
  STRIPE_PRICE_SOLO    — Plan Solo (~150 €/mois)
  STRIPE_PRICE_CABINET — Plan Cabinet
  STRIPE_PRICE_RESEAU  — Plan Réseau
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import stripe

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _init_stripe() -> None:
    """Set the Stripe API key from settings (idempotent)."""
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY


def get_price_id(plan: str) -> str:
    """Map internal plan name to the Stripe Price ID from settings.

    Raises:
        ValueError: if plan is unknown.
    """
    settings = get_settings()
    price_map = {
        "solo":    settings.STRIPE_PRICE_SOLO,
        "cabinet": settings.STRIPE_PRICE_CABINET,
        "reseau":  settings.STRIPE_PRICE_RESEAU,
    }
    price_id = price_map.get(plan)
    if not price_id:
        raise ValueError(f"Plan inconnu : {plan!r}. Valeurs attendues : solo, cabinet, reseau")
    return price_id


def plan_from_price_id(price_id: str) -> str:
    """Reverse-map a Stripe Price ID back to an internal plan name."""
    settings = get_settings()
    reverse = {
        settings.STRIPE_PRICE_SOLO:    "solo",
        settings.STRIPE_PRICE_CABINET: "cabinet",
        settings.STRIPE_PRICE_RESEAU:  "reseau",
    }
    return reverse.get(price_id, "solo")


def get_or_create_customer(
    cabinet_id: str,
    cabinet_nom: str,
    stripe_customer_id: Optional[str],
) -> str:
    """Return existing Stripe customer ID or create a new one.

    Args:
        cabinet_id:         Internal UUID of the cabinet (stored as metadata).
        cabinet_nom:        Display name used as Stripe customer name.
        stripe_customer_id: Existing Stripe customer ID if already created.

    Returns:
        Stripe customer ID (``cus_...``).
    """
    _init_stripe()
    if stripe_customer_id:
        return stripe_customer_id

    customer = stripe.Customer.create(
        name=cabinet_nom,
        metadata={"cabinet_id": cabinet_id},
    )
    log.info("[billing] Stripe customer created: %s for cabinet %s", customer.id, cabinet_id)
    return customer.id


def create_checkout_session(
    stripe_customer_id: str,
    plan: str,
    cabinet_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session for a subscription and return its URL.

    Args:
        stripe_customer_id: Stripe ``cus_...`` ID (already created).
        plan:               Internal plan name (solo / cabinet / reseau).
        cabinet_id:         Stored as subscription metadata for webhook lookup.
        success_url:        Redirect URL on successful payment.
        cancel_url:         Redirect URL if user cancels checkout.

    Returns:
        Stripe Checkout session URL.
    """
    _init_stripe()
    price_id = get_price_id(plan)

    session = stripe.checkout.Session.create(
        customer=stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"cabinet_id": cabinet_id, "plan": plan},
        subscription_data={
            "metadata": {"cabinet_id": cabinet_id, "plan": plan},
        },
    )
    log.info(
        "[billing] Checkout session %s — cabinet=%s plan=%s",
        session.id, cabinet_id, plan,
    )
    return session.url


def create_portal_session(stripe_customer_id: str, return_url: str) -> str:
    """Create a Stripe Customer Portal session and return its URL.

    Args:
        stripe_customer_id: Stripe ``cus_...`` ID.
        return_url:         URL the user is redirected to after leaving the portal.

    Returns:
        Stripe Customer Portal session URL.
    """
    _init_stripe()
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=return_url,
    )
    log.info("[billing] Portal session created for customer %s", stripe_customer_id)
    return session.url


def construct_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and parse a Stripe webhook event.

    Args:
        payload:    Raw request body bytes.
        sig_header: Value of the ``Stripe-Signature`` HTTP header.

    Returns:
        Parsed and verified ``stripe.Event`` object.

    Raises:
        stripe.error.SignatureVerificationError: if signature is invalid.
        ValueError: if webhook secret is not configured.
    """
    settings = get_settings()
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

    return stripe.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )


def parse_subscription_period(
    stripe_sub: dict,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Extract current_period_start / end from a Stripe subscription object."""
    start_ts = stripe_sub.get("current_period_start")
    end_ts = stripe_sub.get("current_period_end")
    start = datetime.fromtimestamp(start_ts, tz=timezone.utc) if start_ts else None
    end = datetime.fromtimestamp(end_ts, tz=timezone.utc) if end_ts else None
    return start, end
