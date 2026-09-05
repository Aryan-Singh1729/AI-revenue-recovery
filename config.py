"""
Configuration module — loads environment variables and defines all constants/thresholds.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
load_dotenv(Path(__file__).parent / ".env")

# ─── Razorpay Test Mode ────────────────────────────────────────────────────────
RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")

# ─── LLM (Groq — open-weight models, OpenAI-compatible endpoint) ──────────────
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
# LLM_API_KEY may be one key, or several comma-separated — extra keys are
# tried in order if an earlier one fails or is rate-limited, which
# effectively multiplies the free-tier request budget.
LLM_API_KEYS: list[str] = [k.strip() for k in os.getenv("LLM_API_KEY", "").split(",") if k.strip()]
LLM_API_KEY: str = LLM_API_KEYS[0] if LLM_API_KEYS else ""  # back-compat single-key accessor
LLM_MODEL: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# ─── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/recovery.db")

# ─── Policy / Stopping Rule Thresholds ─────────────────────────────────────────
MAX_RETRY_ATTEMPTS: int = 3          # Rule 1: No more than 3 payment retries
MAX_COMMUNICATIONS: int = 2          # Rule 2: No more than 2 dunning messages
MAX_RECOVERY_DAYS: int = 14          # Rule 3: Stop recovery after 14 days
MIN_RECOVERY_AMOUNT: int = 50        # Rule 4: Don't recover amounts < ₹50 (in rupees)
COST_RATIO_LIMIT: float = 0.30      # Rule 5: Stop if cost > 30% of amount
COOLDOWN_HOURS: int = 24             # Rule 7: Min 24 hours between actions
HIGH_VALUE_THRESHOLD: int = 25000    # Rule 10: Escalate ₹25,000+ for human review

# ─── Recovery Cost Estimates (in ₹) ────────────────────────────────────────────
COST_PER_RETRY: float = 2.0         # Estimated cost of a retry API call
COST_PER_PAYMENT_LINK: float = 5.0  # Estimated cost of creating a payment link
COST_PER_DUNNING: float = 3.0       # Estimated cost of AI message generation
COST_PER_ESCALATION: float = 0.0    # Escalation itself is free

# ─── App Settings ──────────────────────────────────────────────────────────────
API_HOST: str = "0.0.0.0"
API_PORT: int = 8000
PAYMENT_LINK_EXPIRY_DAYS: int = 7    # Payment links expire after 7 days
