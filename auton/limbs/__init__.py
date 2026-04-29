"""Limbs — Action Interfaces for Project AEON."""

from auton.limbs.base_limb import BaseLimb
from auton.limbs.banking.plaid_client import PlaidLimb
from auton.limbs.banking.reconciler import BankReconciler
from auton.limbs.commerce.stripe_limb import StripeLimb
from auton.limbs.dataclasses import (
    CheckoutSession,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Product,
    TradeOrder,
)
from auton.limbs.human_gateway import (
    ActionExecuted,
    ActionProposed,
    ActionProposal,
    ActionRejected,
    ApprovalStatus,
    HumanGateway,
    HumanGatewayError,
)
from auton.limbs.payments.crypto_onramp import CryptoOnrampLimb
from auton.limbs.payments.stripe_client import StripePaymentsLimb
from auton.limbs.trading.binance_spot_trading import BinanceSpotTradingLimb

try:
    from auton.limbs.web_automation import (
        BrowserController,
        CaptchaSolver,
        FormFiller,
        Navigator,
        ReceiptDownloader,
        SessionManager,
        TaskRecorder,
        WebAction,
        WebActionType,
        WebResult,
    )
except ImportError:
    BrowserController = None  # type: ignore[misc, assignment]
    CaptchaSolver = None  # type: ignore[misc, assignment]
    FormFiller = None  # type: ignore[misc, assignment]
    Navigator = None  # type: ignore[misc, assignment]
    ReceiptDownloader = None  # type: ignore[misc, assignment]
    SessionManager = None  # type: ignore[misc, assignment]
    TaskRecorder = None  # type: ignore[misc, assignment]
    WebAction = None  # type: ignore[misc, assignment]
    WebActionType = None  # type: ignore[misc, assignment]
    WebResult = None  # type: ignore[misc, assignment]

__all__ = [
    "ActionExecuted",
    "ActionProposed",
    "ActionProposal",
    "ActionRejected",
    "ApprovalStatus",
    "BaseLimb",
    "BankReconciler",
    "BinanceSpotTradingLimb",
    "BrowserController",
    "CaptchaSolver",
    "CheckoutSession",
    "CryptoOnrampLimb",
    "FormFiller",
    "HumanGateway",
    "HumanGatewayError",
    "Navigator",
    "OrderResult",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PlaidLimb",
    "Product",
    "ReceiptDownloader",
    "SessionManager",
    "StripeLimb",
    "StripePaymentsLimb",
    "TaskRecorder",
    "TradeOrder",
    "WebAction",
    "WebActionType",
    "WebResult",
]
