#!/usr/bin/env bash
# =============================================================================
# Project ÆON — Installation & Brain Initialization Script
# =============================================================================
# Usage:
#   curl -fsSL <url>/install.sh | bash           # interactive setup
#   curl -fsSL <url>/install.sh | bash -s -- --non-interactive  # skip prompts
#   ./install.sh --help
#
# Works on macOS (brew) and Linux (apt).  Python 3.11+ required.
# Safe to run multiple times — idempotent by design.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m';    GREEN='\033[0;32m';    YELLOW='\033[0;33m'
BLUE='\033[0;34m';   MAGENTA='\033[0;35m';  CYAN='\033[0;36m'
BOLD='\033[1m';      DIM='\033[2m';          RESET='\033[0m'

red()    { echo -e "${RED}$*${RESET}"; }
green()  { echo -e "${GREEN}$*${RESET}"; }
yellow() { echo -e "${YELLOW}$*${RESET}"; }
cyan()   { echo -e "${CYAN}$*${RESET}"; }
bold()   { echo -e "${BOLD}$*${RESET}"; }
dim()    { echo -e "${DIM}$*${RESET}"; }

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
NON_INTERACTIVE=false
PROJECT_DIR=""
ENV_FILE=""
VENV_PYTHON=""
SKIP_DEPS=false

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
banner() {
    echo
    echo -e "${CYAN}  ╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}  ║${RESET}     ${BOLD}Project ÆON — Autonomous Economic Node${RESET}        ${CYAN}║${RESET}"
    echo -e "${CYAN}  ║${RESET}            ${DIM}Installation & Brain Init${RESET}              ${CYAN}║${RESET}"
    echo -e "${CYAN}  ╚══════════════════════════════════════════════════╝${RESET}"
    echo
}

# ---------------------------------------------------------------------------
# Phase 1 — Pre-flight checks
# ---------------------------------------------------------------------------
phase1_preflight() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 1/5  — Pre-flight checks${RESET}"
    echo

    # --- OS detection ---
    OS="unknown"
    case "$(uname -s)" in
        Darwin)  OS="macos" ;;
        Linux)   OS="linux" ;;
        *)
            red "Unsupported OS: $(uname -s).  macOS or Linux required."
            exit 1
            ;;
    esac
    echo -e "  ${GREEN}✓${RESET} OS: ${BOLD}${OS}${RESET}"

    # --- Python 3.11+ ---
    PYTHON=""
    for candidate in python3 python; do
        if cmd="$(\command -v "$candidate" 2>/dev/null)"; then
            ver="$("$cmd" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)"
            if [[ "$ver" == "(3, 11)" || "$ver" == "(3, 12)" || "$ver" == "(3, 13)" || "$ver" == "(3, 14)" || "$ver" == "(3, 15)" ]]; then
                PYTHON="$cmd"
                break
            fi
        fi
    done

    if [[ -z "$PYTHON" ]]; then
        red "Python 3.11+ is required but was not found."
        echo "Install it via:"
        if [[ "$OS" == "macos" ]]; then
            echo "  brew install python@3.12"
        else
            echo "  sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip"
        fi
        exit 1
    fi
    echo -e "  ${GREEN}✓${RESET} Python: ${BOLD}$("$PYTHON" --version)${RESET} ($PYTHON)"

    # --- pip ---
    if ! "$PYTHON" -m pip --version &>/dev/null; then
        red "pip is not available for $PYTHON.  Install python3-pip or equivalent."
        exit 1
    fi
    echo -e "  ${GREEN}✓${RESET} pip: available"

    # --- git ---
    if ! command -v git &>/dev/null; then
        if [[ "$NON_INTERACTIVE" == true ]]; then
            red "git is required but not found."
            exit 1
        fi
        echo -ne "  ${YELLOW}○${RESET} git not found. Install now? [Y/n] "
        read -r ans
        if [[ "$ans" != "n" && "$ans" != "N" ]]; then
            if [[ "$OS" == "macos" ]]; then
                brew install git
            else
                sudo apt update && sudo apt install -y git
            fi
        fi
    fi
    echo -e "  ${GREEN}✓${RESET} git: $(git --version 2>/dev/null | head -1 || echo 'available')"

    echo
}

# ---------------------------------------------------------------------------
# Phase 2 — Project setup
# ---------------------------------------------------------------------------
phase2_project_setup() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 2/5  — Project setup${RESET}"
    echo

    # Determine project directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Check if we're already in the ÆON project
    if [[ -f "$SCRIPT_DIR/auton/aeon.py" ]] && [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        PROJECT_DIR="$SCRIPT_DIR"
        echo -e "  ${GREEN}✓${RESET} Already inside Project ÆON: ${DIM}$PROJECT_DIR${RESET}"
    else
        # We need to find or clone the project
        PROJECT_DIR="${AEON_HOME:-$HOME/.aeon}"

        if [[ -d "$PROJECT_DIR" ]] && [[ -f "$PROJECT_DIR/auton/aeon.py" ]]; then
            echo -e "  ${GREEN}✓${RESET} Found existing install at: ${DIM}$PROJECT_DIR${RESET}"
        else
            echo -e "  ${YELLOW}○${RESET} Project not found locally."

            if [[ "$NON_INTERACTIVE" == true ]]; then
                red "Cannot clone — non-interactive mode and project not found. Set AEON_HOME to an existing install."
                exit 1
            fi

            echo -ne "  Clone from GitHub? Enter URL or 'skip' if already cloned: "
            read -r clone_url
            if [[ "$clone_url" != "skip" ]]; then
                mkdir -p "$PROJECT_DIR"
                git clone "$clone_url" "$PROJECT_DIR"
                echo -e "  ${GREEN}✓${RESET} Cloned to: ${DIM}$PROJECT_DIR${RESET}"
            else
                echo -ne "  Enter path to existing ÆON project: "
                read -r existing_path
                PROJECT_DIR="$existing_path"
            fi
        fi
    fi

    cd "$PROJECT_DIR"
    ENV_FILE="$PROJECT_DIR/.env"

    # --- Virtual environment ---
    if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
        echo -e "  ${YELLOW}○${RESET} Creating virtual environment..."
        "$PYTHON" -m venv "$PROJECT_DIR/.venv"
        echo -e "  ${GREEN}✓${RESET} Virtual environment created"
    else
        echo -e "  ${GREEN}✓${RESET} Virtual environment: ${DIM}.venv/${RESET}"
    fi

    VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
    VENV_PIP="$PROJECT_DIR/.venv/bin/pip"

    # --- Upgrade pip ---
    "$VENV_PIP" install --upgrade pip setuptools wheel -q
    echo -e "  ${GREEN}✓${RESET} pip/setuptools/wheel up to date"

    # --- Install requirements ---
    if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
        echo -e "  ${YELLOW}○${RESET} Installing Python dependencies..."
        "$VENV_PIP" install -r "$PROJECT_DIR/requirements.txt" -q
        echo -e "  ${GREEN}✓${RESET} Dependencies installed"
    fi

    # --- Playwright browsers ---
    if "$VENV_PIP" show playwright &>/dev/null; then
        echo -e "  ${YELLOW}○${RESET} Installing Playwright Chromium..."
        "$VENV_PYTHON" -m playwright install chromium --with-deps 2>/dev/null || true
        echo -e "  ${GREEN}✓${RESET} Playwright ready"
    fi

    # --- data directory ---
    mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/cold_storage"
    echo -e "  ${GREEN}✓${RESET} Runtime directories ready"

    echo
}

# ---------------------------------------------------------------------------
# Phase 3 — API key wizard
# ---------------------------------------------------------------------------
phase3_api_wizard() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 3/5  — API key configuration${RESET}"
    echo

    if [[ "$NON_INTERACTIVE" == true ]]; then
        echo -e "  ${YELLOW}○${RESET} Non-interactive mode — creating .env with empty placeholders."
        if [[ ! -f "$ENV_FILE" ]]; then
            cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
        fi
        echo -e "  ${GREEN}✓${RESET} Edit ${DIM}.env${RESET} to fill in your keys."
        echo
        return
    fi

    # Seed .env from .env.example if it doesn't exist
    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    fi

    # Helper: prompt for a key
    ask_key() {
        local var="$1" desc="$2" url="$3" required="$4"
        local current
        current="$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"

        local tag="optional"
        [[ "$required" == "true" ]] && tag="required"

        echo
        echo -e "  ${BOLD}${var}${RESET} ${DIM}(${tag})${RESET}"
        echo -e "  ${DIM}${desc}${RESET}"
        [[ -n "$url" ]] && echo -e "  ${DIM}Get it at: ${url}${RESET}"

        if [[ -n "$current" ]]; then
            local masked
            if [[ ${#current} -gt 4 ]]; then
                masked="${current:0:2}...${current: -2}"
            else
                masked="****"
            fi
            echo -ne "  Current: ${DIM}${masked}${RESET} — change? [y/N] "
            read -r change
            if [[ "$change" != "y" && "$change" != "Y" ]]; then
                echo -e "  ${GREEN}✓${RESET} keeping existing value"
                return
            fi
        elif [[ "$required" == "true" ]]; then
            echo -ne "  ${YELLOW}Value (required):${RESET} "
        else
            echo -ne "  ${DIM}Value (press enter to skip):${RESET} "
        fi

        read -r value
        if [[ -n "$value" ]]; then
            _set_env_value "$var" "$value"
            echo -e "  ${GREEN}✓${RESET} saved"
        elif [[ "$required" == "true" ]]; then
            echo -e "  ${YELLOW}⚠${RESET} left blank — some features will be disabled"
        fi
    }

    _set_env_value() {
        local var="$1" value="$2"
        if grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
            if [[ "$OS" == "macos" ]]; then
                sed -i '' "s|^${var}=.*|${var}=${value}|" "$ENV_FILE"
            else
                sed -i "s|^${var}=.*|${var}=${value}|" "$ENV_FILE"
            fi
        else
            echo "${var}=${value}" >> "$ENV_FILE"
        fi
    }

    # --- Core ---
    echo -e "  ${CYAN}┌─ Core ──────────────────────────────────────────┐${RESET}"
    ask_key "AEON_RESTRICTED_MODE" "Restricted mode requires human approval for all actions" "" false
    if [[ -z "$(grep "^AEON_RESTRICTED_MODE=" "$ENV_FILE" | cut -d= -f2-)" ]]; then
        _set_env_value "AEON_RESTRICTED_MODE" "false"
    fi

    # --- Email SMTP ---
    echo -e "  ${CYAN}┌─ Email (SMTP) — for approval requests ──────────┐${RESET}"
    ask_key "AEON_APPROVAL_EMAIL_SMTP_HOST" "SMTP server hostname" "" false
    ask_key "AEON_APPROVAL_EMAIL_SMTP_PORT" "SMTP port" "" false
    ask_key "AEON_APPROVAL_EMAIL_SENDER" "From: address" "" false
    ask_key "AEON_APPROVAL_EMAIL_PASSWORD" "SMTP password or app-specific password" "" false
    ask_key "AEON_APPROVAL_EMAIL_RECIPIENT" "Human operator's email" "" false
    ask_key "AEON_APPROVAL_EMAIL_USE_TLS" "Use STARTTLS (true/false)" "" false

    # --- Email IMAP ---
    echo -e "  ${CYAN}┌─ Email (IMAP) — for reading responses ──────────┐${RESET}"
    ask_key "AEON_EMAIL_IMAP_HOST" "IMAP server hostname" "" false
    ask_key "AEON_EMAIL_IMAP_PORT" "IMAP port (default 993)" "" false
    ask_key "AEON_EMAIL_USER" "IMAP username/email" "" false
    ask_key "AEON_EMAIL_PASSWORD" "IMAP password or app password" "" false

    # --- Vault ---
    echo -e "  ${CYAN}┌─ Vault encryption ──────────────────────────────┐${RESET}"
    local vault_key
    vault_key="$(grep "^AEON_VAULT_KEY=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
    if [[ -z "$vault_key" ]]; then
        vault_key="$("$VENV_PYTHON" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        _set_env_value "AEON_VAULT_KEY" "$vault_key"
        echo -e "  ${GREEN}✓${RESET} Vault key auto-generated"
    else
        echo -e "  ${GREEN}✓${RESET} Vault key: ${DIM}already set${RESET}"
    fi

    # --- Exchanges ---
    echo -e "  ${CYAN}┌─ Exchanges ─────────────────────────────────────┐${RESET}"
    ask_key "BINANCE_API_KEY" "Binance API key" "https://www.binance.com/en/support/faq/how-to-create-api-keys" false
    ask_key "BINANCE_SECRET_KEY" "Binance secret key" "" false
    ask_key "COINBASE_API_KEY" "Coinbase API key" "https://www.coinbase.com/settings/api" false
    ask_key "COINBASE_SECRET_KEY" "Coinbase secret key" "" false

    # --- Payments ---
    echo -e "  ${CYAN}┌─ Payments (Stripe) ─────────────────────────────┐${RESET}"
    ask_key "STRIPE_SECRET_KEY" "Stripe secret key" "https://dashboard.stripe.com/apikeys" false
    ask_key "STRIPE_WEBHOOK_SECRET" "Stripe webhook signing secret" "" false

    # --- Banking ---
    echo -e "  ${CYAN}┌─ Banking (Plaid) ───────────────────────────────┐${RESET}"
    ask_key "PLAID_CLIENT_ID" "Plaid client ID" "https://dashboard.plaid.com/keys" false
    ask_key "PLAID_SECRET" "Plaid secret" "" false
    ask_key "PLAID_ENV" "Plaid environment (sandbox/development/production)" "" false

    # --- Intelligence ---
    echo -e "  ${CYAN}┌─ Intelligence & search ─────────────────────────┐${RESET}"
    ask_key "SERPAPI_KEY" "SerpAPI key for web search" "https://serpapi.com/manage-api-key" false
    ask_key "BRAVE_API_KEY" "Brave Search API key" "https://brave.com/search/api/" false
    ask_key "TWITTER_API_KEY" "Twitter/X API key" "https://developer.twitter.com/en/portal/dashboard" false
    ask_key "TWITTER_API_SECRET" "Twitter/X API secret" "" false

    # --- AI/LLM ---
    echo -e "  ${CYAN}┌─ AI / LLM providers ────────────────────────────┐${RESET}"
    ask_key "ANTHROPIC_API_KEY" "Anthropic (Claude) API key" "https://console.anthropic.com/settings/keys" false
    ask_key "OPENAI_API_KEY" "OpenAI (GPT) API key" "https://platform.openai.com/api-keys" false

    # --- Deployment ---
    echo -e "  ${CYAN}┌─ Deployment & marketplace ──────────────────────┐${RESET}"
    ask_key "RENDER_API_KEY" "Render API key for cloud hosting" "https://dashboard.render.com/u/settings/api-keys" false
    ask_key "GUMROAD_API_KEY" "Gumroad API key for marketplace" "https://app.gumroad.com/settings/advanced" false

    echo
    echo -e "  ${GREEN}✓${RESET} Configuration saved to ${DIM}.env${RESET}"
    echo
}

# ---------------------------------------------------------------------------
# Phase 4 — Initialize the brain
# ---------------------------------------------------------------------------
phase4_initialize() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 4/5  — Brain initialization${RESET}"
    echo

    # Source .env so the child process inherits values
    set -a; source "$ENV_FILE"; set +a

    # Ensure vault key is available
    if [[ -z "${AEON_VAULT_KEY:-}" ]]; then
        export AEON_VAULT_KEY
        AEON_VAULT_KEY="$("$VENV_PYTHON" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        _set_env_value "AEON_VAULT_KEY" "$AEON_VAULT_KEY"
    fi

    # Quick smoke-test: initialize AEON, let it create databases, then exit
    echo -e "  ${YELLOW}○${RESET} Initializing consciousness database..."
    "$VENV_PYTHON" -c "
import os, sys
os.environ.setdefault('AEON_VAULT_KEY', '${AEON_VAULT_KEY}')
from auton.core.consciousness import Consciousness
c = Consciousness(db_path='data/consciousness.db')
c.remember('installation', {'phase': 'brain_init', 'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'}, importance=1.0)
summary = c.get_consciousness_summary()
print(f'  Memory count: {summary[\"total_memories\"]}')
print(f'  DB size: {summary[\"db_size_bytes\"]} bytes')
" 2>&1 || {
        yellow "  ⚠ Consciousness DB may already exist — continuing"
    }
    echo -e "  ${GREEN}✓${RESET} Consciousness database ready"

    # Smoke-test: ledger
    echo -e "  ${YELLOW}○${RESET} Initializing ledger..."
    "$VENV_PYTHON" -c "
import os
os.environ.setdefault('AEON_VAULT_KEY', '${AEON_VAULT_KEY}')
from auton.ledger.master_wallet import MasterWallet
w = MasterWallet(db_path='data/aeon_ledger.db')
bal = w.get_balance()
print(f'  Ledger balance: \${bal:.2f}')
" 2>&1 || {
        yellow "  ⚠ Ledger may already be initialized — continuing"
    }
    echo -e "  ${GREEN}✓${RESET} Ledger ready"

    echo
    echo -e "  ${GREEN}✓${RESET} Brain initialized successfully"
    echo
}

# ---------------------------------------------------------------------------
# Phase 5 — Launch options
# ---------------------------------------------------------------------------
phase5_launch() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 5/5  — Launch${RESET}"
    echo

    if [[ "$NON_INTERACTIVE" == true ]]; then
        echo -e "  ${GREEN}✓${RESET} Setup complete.  Start ÆON with:"
        echo
        echo -e "    ${BOLD}cd ${PROJECT_DIR} && .venv/bin/python -m auton.aeon${RESET}"
        echo
        echo -e "  Or via Docker: ${BOLD}docker-compose up${RESET}"
        echo
        return
    fi

    echo -e "  ÆON is ready to boot."
    echo
    echo -e "  ${BOLD}Launch options:${RESET}"
    echo -e "    ${CYAN}[1]${RESET} Start in foreground (attach to terminal)"
    echo -e "    ${CYAN}[2]${RESET} Start in background (daemon)"
    echo -e "    ${CYAN}[3]${RESET} Start via Docker Compose"
    echo -e "    ${CYAN}[4]${RESET} Skip — I'll launch manually"
    echo
    echo -ne "  Choice [1-4, default 4]: "
    read -r choice

    case "${choice:-4}" in
        1)
            echo
            echo -e "  ${GREEN}▶${RESET} Booting ÆON in foreground..."
            echo -e "  ${DIM}Press Ctrl+C to stop${RESET}"
            echo
            cd "$PROJECT_DIR"
            set -a; source "$ENV_FILE"; set +a
            export AEON_VAULT_KEY
            exec "$VENV_PYTHON" -m auton.aeon
            ;;
        2)
            echo -e "  ${YELLOW}○${RESET} Starting in background..."
            cd "$PROJECT_DIR"
            set -a; source "$ENV_FILE"; set +a
            export AEON_VAULT_KEY
            nohup "$VENV_PYTHON" -m auton.aeon > data/aeon.log 2>&1 &
            echo $! > data/aeon.pid
            echo -e "  ${GREEN}✓${RESET} PID: $(cat data/aeon.pid)"
            echo -e "  ${GREEN}✓${RESET} Logs: ${DIM}data/aeon.log${RESET}"
            echo -e "  ${DIM}Use 'aeonctl stop' to halt it${RESET}"
            ;;
        3)
            echo
            if command -v docker &>/dev/null && [[ -f "$PROJECT_DIR/docker-compose.yml" ]]; then
                cd "$PROJECT_DIR"
                docker-compose up -d
                echo -e "  ${GREEN}✓${RESET} Docker container started"
            else
                red "  Docker or docker-compose not found."
            fi
            ;;
        *)
            echo
            echo -e "  ${GREEN}✓${RESET} Setup complete.  Start ÆON manually:"
            echo
            echo -e "    ${BOLD}cd ${PROJECT_DIR} && .venv/bin/python -m auton.aeon${RESET}"
            echo -e "    ${BOLD}docker-compose up${RESET}"
            echo -e "    ${BOLD}./aeonctl start${RESET}"
            echo
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    # Parse flags
    for arg in "$@"; do
        case "$arg" in
            --non-interactive|-n) NON_INTERACTIVE=true ;;
            --skip-deps)          SKIP_DEPS=true ;;
            --help|-h)
                echo "Usage: ./install.sh [--non-interactive] [--skip-deps]"
                echo
                echo "  --non-interactive  Skip all prompts, create .env from template"
                echo "  --skip-deps        Skip Python dependency installation"
                echo
                exit 0
                ;;
        esac
    done

    banner
    phase1_preflight
    phase2_project_setup
    phase3_api_wizard
    phase4_initialize
    phase5_launch

    echo -e "${GREEN}  ═══════════════════════════════════════════════════${RESET}"
    echo -e "  ${BOLD}ÆON is ready.${RESET}  Manage it with: ${CYAN}./aeonctl${RESET}"
    echo -e "  Commands: ${DIM}status | history | start | stop | chat${RESET}"
    echo -e "${GREEN}  ═══════════════════════════════════════════════════${RESET}"
    echo
}

main "$@"
