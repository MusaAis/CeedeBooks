"""
Central config for the CeedeBooks agent. Loads and validates environment variables.
Laya-related settings are present but intentionally not required yet — that
integration is deferred to a later phase (see LAYA_ENABLED).
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


_ARC_TESTNET_USDC = "0x3600000000000000000000000000000000000000"


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # Arc Testnet
    arc_rpc_url: str
    deployer_private_key: str
    agent_address: str
    approver_address: str
    usdc_address: str
    budget_enforcer_address: str
    milestone_escrow_address: str
    ceedebooks_yield_address: str

    # Circle
    circle_api_key: str
    circle_entity_secret: str
    circle_treasury_wallet_id: str
    circle_treasury_wallet_address: str
    gateway_api_url: str

    # AI models
    groq_api_key: str
    groq_llm_model: str

    # Laya — deferred; not required for Phase 1
    laya_enabled: bool
    laya_model: str
    laya_finetuned_checkpoint_path: str

    # CCTP
    cctp_arc_domain: int
    cctp_min_finality_threshold: int

    # Budget limits (defaults mirrored on-chain via BudgetEnforcer.setBudget)
    daily_budget_usdc: float
    per_tx_limit_usdc: float
    usyc_idle_threshold_days: int

    # App
    backend_url: str
    db_path: str

    # API hardening
    max_body_bytes: int
    rate_limit_per_min: int
    failed_auth_per_min: int
    cors_origins: tuple
    trust_proxy: bool

    @classmethod
    def load(cls) -> "Config":
        return cls(
            arc_rpc_url=_require("ARC_TESTNET_RPC_URL"),
            deployer_private_key=_require("DEPLOYER_PRIVATE_KEY"),
            agent_address=_optional("AGENT_ADDRESS"),
            approver_address=_optional("APPROVER_ADDRESS"),
            usdc_address=_optional("USDC_ADDRESS") or _ARC_TESTNET_USDC,
            budget_enforcer_address=_optional("BUDGET_ENFORCER_ADDRESS"),
            milestone_escrow_address=_optional("MILESTONE_ESCROW_ADDRESS"),
            ceedebooks_yield_address=_optional("CEEDEBOOKS_YIELD_ADDRESS"),
            circle_api_key=_optional("CIRCLE_API_KEY"),
            circle_entity_secret=_optional("CIRCLE_ENTITY_SECRET"),
            circle_treasury_wallet_id=_optional("CIRCLE_TREASURY_WALLET_ID"),
            circle_treasury_wallet_address=_optional("CIRCLE_TREASURY_WALLET_ADDRESS"),
            gateway_api_url=_optional("GATEWAY_API_URL", "https://gateway-api-testnet.circle.com"),
            groq_api_key=_require("GROQ_API_KEY"),
            groq_llm_model=_optional("GROQ_LLM_MODEL", "openai/gpt-oss-120b"),
            laya_enabled=bool(_optional("LAYA_FINETUNED_CHECKPOINT_PATH")),
            laya_model=_optional("LAYA_MODEL", "convaiinnovations/laya-typed-decisions"),
            laya_finetuned_checkpoint_path=_optional("LAYA_FINETUNED_CHECKPOINT_PATH"),
            cctp_arc_domain=int(_optional("CCTP_ARC_DOMAIN", "26")),
            cctp_min_finality_threshold=int(_optional("CCTP_MIN_FINALITY_THRESHOLD", "2000")),
            daily_budget_usdc=float(_optional("DAILY_BUDGET_USDC", "100.0")),
            per_tx_limit_usdc=float(_optional("PER_TX_LIMIT_USDC", "20.0")),
            usyc_idle_threshold_days=int(_optional("USYC_IDLE_THRESHOLD_DAYS", "3")),
            backend_url=_optional("BACKEND_URL", "https://api.ceedebooks.xyz"),
            db_path=_optional("CEEDEBOOKS_DB_PATH", "./ceedebooks.db"),
            max_body_bytes=int(_optional("MAX_BODY_BYTES") or "16384"),
            rate_limit_per_min=int(_optional("RATE_LIMIT_PER_MIN") or "60"),
            failed_auth_per_min=int(_optional("FAILED_AUTH_PER_MIN") or "10"),
            cors_origins=tuple(
                o.strip() for o in (_optional("CORS_ORIGINS") or "https://ceedebooks.xyz").split(",") if o.strip()
            ),
            trust_proxy=_optional("TRUST_PROXY").lower() in ("1", "true", "yes"),
        )


config = Config.load()

