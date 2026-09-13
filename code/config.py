"""
config.py — central configuration for the Buy-or-Wait agent.

All secrets come from environment variables. Nothing here is a credential.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
MEDIA_DIR = DATASET_DIR / "media" / "images"
CACHE_DIR = REPO_ROOT / ".cache"
EVAL_DIR = REPO_ROOT / "evaluation"

REQUESTS_CSV = DATASET_DIR / "requests.csv"
SAMPLE_REQUESTS_CSV = DATASET_DIR / "sample_requests.csv"
PROFILES_CSV = DATASET_DIR / "financial_profiles.csv"
EVENTS_CSV = DATASET_DIR / "financial_events.csv"
EXCHANGE_RATES_CSV = DATASET_DIR / "exchange_rates.csv"
PAYMENT_OPTIONS_CSV = DATASET_DIR / "request_payment_options.csv"
MESSAGES_CSV = DATASET_DIR / "messages.csv"
IMAGES_CSV = DATASET_DIR / "images.csv"

OUTPUT_CSV = REPO_ROOT / "output.csv"
AGENTS_LOG_MD = REPO_ROOT / "gemini.md"
USAGE_REPORT_MD = EVAL_DIR / "usage_report.md"
IMAGE_EVIDENCE_CACHE = CACHE_DIR / "image_evidence_cache.json"

# ---------------------------------------------------------------------------
# LLM configuration (Gemini). Never hardcode the key.
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", GEMINI_MODEL)

# Bounded retries for LLM calls.
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "2"))
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))

# Agent loop bound.
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "12"))

# ---------------------------------------------------------------------------
# Financial engine constants
# ---------------------------------------------------------------------------
FORECAST_HORIZON_DAYS = 90
MONEY_QUANT = "0.01"  # 2 decimal places for all currencies in this dataset

# Allowed output enums (contract is fixed; never introduce new values).
AFFORDABILITY_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}
PAYMENT_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

MAX_SPENDING_CHANGES = 3

# Event classification helpers
CANCELLED_STATUSES = {"cancelled", "failed", "unrealized"}
COMMITTED_STATUSES = {"settled", "pending", "scheduled"}
NON_CASH_DIRECTIONS = {"non_cash"}

FLEXIBLE_REDUCIBLE = {"reducible", "reducible_or_stoppable"}
FLEXIBLE_STOPPABLE = {"stoppable", "reducible_or_stoppable"}
