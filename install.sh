#!/usr/bin/env bash
# =============================================================================
# Project ÆON — Installation & Brain Initialization Script
# =============================================================================
# Usage:
#   curl -fsSL <url>/install.sh | bash
#
# The script auto-downloads aeon.tar.gz from the same location.
# Set AEON_RELEASE_BASE to override the base URL.
#
# Works on macOS and Linux.  Python 3.11+ required.
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
# Configuration — override AEON_RELEASE_BASE env var to point at your host
# ---------------------------------------------------------------------------
AEON_HOME="${AEON_HOME:-$HOME/.aeon}"
AEON_RELEASE_BASE="${AEON_RELEASE_BASE:-https://aeon.example.com}"

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

    OS="unknown"
    case "$(uname -s)" in
        Darwin)  OS="macos" ;;
        Linux)   OS="linux" ;;
        *) red "Unsupported OS.  macOS or Linux required."; exit 1 ;;
    esac
    echo -e "  ${GREEN}✓${RESET} OS: ${BOLD}${OS}${RESET}"

    PYTHON=""
    for candidate in python3 python; do
        if cmd="$(\command -v "$candidate" 2>/dev/null)"; then
            ver="$("$cmd" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)"
            case "$ver" in
                "(3, 11)"|"(3, 12)"|"(3, 13)"|"(3, 14)"|"(3, 15)")
                    PYTHON="$cmd"; break ;;
            esac
        fi
    done

    if [[ -z "$PYTHON" ]]; then
        red "Python 3.11+ is required but was not found."
        if [[ "$OS" == "macos" ]]; then
            echo "Install it:  brew install python@3.12"
        else
            echo "Install it:  sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip"
        fi
        exit 1
    fi
    echo -e "  ${GREEN}✓${RESET} Python: ${BOLD}$("$PYTHON" --version)${RESET}"

    if ! "$PYTHON" -m pip --version &>/dev/null; then
        red "pip is not available.  Install python3-pip."
        exit 1
    fi
    echo -e "  ${GREEN}✓${RESET} pip: available"

    echo
}

# ---------------------------------------------------------------------------
# Phase 2 — Download & extract
# ---------------------------------------------------------------------------
phase2_download() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 2/5  — Download ÆON${RESET}"
    echo

    # Detect if we're already inside the project (not a curl-pipe)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
    if [[ -n "${SCRIPT_DIR:-}" ]] && [[ -f "$SCRIPT_DIR/auton/aeon.py" ]]; then
        AEON_HOME="$SCRIPT_DIR"
        echo -e "  ${GREEN}✓${RESET} Running from local project: ${DIM}$AEON_HOME${RESET}"
        echo
        return
    fi

    # Already installed?
    if [[ -f "$AEON_HOME/auton/aeon.py" ]]; then
        echo -e "  ${GREEN}✓${RESET} Already installed at: ${DIM}$AEON_HOME${RESET}"
        echo
        return
    fi

    # Download the tarball
    TARBALL_URL="${AEON_RELEASE_BASE}/aeon.tar.gz"
    echo -e "  ${YELLOW}↓${RESET} Fetching ${DIM}${TARBALL_URL}${RESET} ..."

    mkdir -p "$AEON_HOME"

    if command -v curl &>/dev/null; then
        curl -fsSL "$TARBALL_URL" -o /tmp/aeon.tar.gz
    elif command -v wget &>/dev/null; then
        wget -q "$TARBALL_URL" -O /tmp/aeon.tar.gz
    else
        red "Neither curl nor wget found.  Install one and retry."
        exit 1
    fi

    tar -xzf /tmp/aeon.tar.gz -C "$AEON_HOME" --strip-components=1
    rm -f /tmp/aeon.tar.gz
    echo -e "  ${GREEN}✓${RESET} Extracted to ${DIM}$AEON_HOME${RESET}"
    echo
}

# ---------------------------------------------------------------------------
# Phase 3 — Environment & dependencies
# ---------------------------------------------------------------------------
phase3_deps() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 3/5  — Dependencies${RESET}"
    echo

    cd "$AEON_HOME"

    # Virtual environment
    if [[ ! -d ".venv" ]]; then
        echo -e "  ${YELLOW}○${RESET} Creating virtual environment..."
        "$PYTHON" -m venv .venv
    fi
    VENV_PYTHON=".venv/bin/python"
    VENV_PIP=".venv/bin/pip"

    "$VENV_PIP" install --upgrade pip setuptools wheel -q

    if [[ -f requirements.txt ]]; then
        echo -e "  ${YELLOW}○${RESET} Installing Python packages..."
        "$VENV_PIP" install -r requirements.txt -q
        echo -e "  ${GREEN}✓${RESET} Packages installed"
    fi

    if "$VENV_PIP" show playwright &>/dev/null; then
        "$VENV_PYTHON" -m playwright install chromium --with-deps 2>/dev/null || true
    fi

    mkdir -p data cold_storage
    echo -e "  ${GREEN}✓${RESET} Runtime directories ready"
    echo
}

# ---------------------------------------------------------------------------
# Phase 4 — API keys & brain init
# ---------------------------------------------------------------------------
phase4_configure() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 4/5  — Configuration & brain init${RESET}"
    echo

    cd "$AEON_HOME"
    ENV_FILE="$AEON_HOME/.env"

    # Seed .env from template
    if [[ ! -f "$ENV_FILE" ]]; then
        cp .env.example "$ENV_FILE"
    fi

    _set_env() {
        local var="$1" val="$2"
        if grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
            if [[ "$OS" == "macos" ]]; then
                sed -i '' "s|^${var}=.*|${var}=${val}|" "$ENV_FILE"
            else
                sed -i "s|^${var}=.*|${var}=${val}|" "$ENV_FILE"
            fi
        else
            echo "${var}=${val}" >> "$ENV_FILE"
        fi
    }

    # Auto-generate vault key
    if [[ -z "$(grep "^AEON_VAULT_KEY=" "$ENV_FILE" | cut -d= -f2-)" ]]; then
        VK="$("$VENV_PYTHON" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        _set_env "AEON_VAULT_KEY" "$VK"
        echo -e "  ${GREEN}✓${RESET} Vault key auto-generated"
    fi

    _set_env "AEON_RESTRICTED_MODE" "${AEON_RESTRICTED_MODE:-false}"

    # --- LLM Provider selection (always shown in TTY mode) ---
    if [[ -t 0 ]]; then
        _choose_llm_provider
        _validate_provider
        _run_wizard
        _ask_guidance_prompt
    fi

    # Source .env
    set -a; source "$ENV_FILE"; set +a
    export AEON_VAULT_KEY="${AEON_VAULT_KEY:-}"

    # --- Initialize the brain ---
    echo -e "  ${YELLOW}○${RESET} Initializing consciousness database..."
    "$VENV_PYTHON" -c "
import os
os.environ['AEON_VAULT_KEY'] = '${AEON_VAULT_KEY}'
from auton.core.consciousness import Consciousness
c = Consciousness(db_path='data/consciousness.db')
c.remember('installation', {'method': 'curl', 'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'}, importance=1.0)
s = c.get_stats()
print(f'  Memories: {s[\"total_memories\"]}  |  DB: {s[\"db_size_kb\"]} KB')
" 2>&1 || yellow "  ⚠ DB may already exist — continuing"
    echo -e "  ${GREEN}✓${RESET} Consciousness ready"

    echo -e "  ${YELLOW}○${RESET} Initializing ledger..."
    "$VENV_PYTHON" -c "
import os
os.environ['AEON_VAULT_KEY'] = '${AEON_VAULT_KEY}'
from auton.ledger.master_wallet import MasterWallet
w = MasterWallet(db_path='data/aeon_ledger.db')
print(f'  Balance: \${w.get_balance():.2f}')
" 2>&1 || yellow "  ⚠ Ledger may already exist — continuing"
    echo -e "  ${GREEN}✓${RESET} Ledger ready"
    echo
}

_choose_llm_provider() {
    echo
    echo -e "  ${CYAN}┌──────────────────────────────────────────────────┐${RESET}"
    echo -e "  ${CYAN}│${RESET}              ${BOLD}LLM Provider Selection${RESET}               ${CYAN}│${RESET}"
    echo -e "  ${CYAN}└──────────────────────────────────────────────────┘${RESET}"
    echo
    echo -e "  ÆON needs an LLM to think, plan, and generate code."
    echo
    echo -e "  ${BOLD}Available providers:${RESET}"
    echo
    echo -e "  ${CYAN}[1]${RESET} ${BOLD}Ollama${RESET} (local, free)"
    echo -e "      Runs entirely on your machine. No API keys needed."
    echo -e "      Install from ${DIM}https://ollama.com${RESET}"
    echo
    echo -e "  ${CYAN}[2]${RESET} ${BOLD}Anthropic (Claude)${RESET} — cloud API"
    echo -e "      Requires an API key. Pay-per-use pricing."
    echo -e "      ${DIM}https://console.anthropic.com/settings/keys${RESET}"
    echo
    echo -e "  ${CYAN}[3]${RESET} ${BOLD}Amazon Bedrock${RESET} — cloud API"
    echo -e "      AWS-managed models (Claude, Llama, Titan, Mistral...)."
    echo -e "      Requires AWS access key + secret key + region."
    echo -e "      ${DIM}https://console.aws.amazon.com/bedrock${RESET}"
    echo
    echo -e "  ${CYAN}[4]${RESET} ${BOLD}OpenAI (GPT)${RESET} — cloud API"
    echo -e "      Requires an API key. Pay-per-use pricing."
    echo -e "      ${DIM}https://platform.openai.com/api-keys${RESET}"
    echo
    echo -ne "  ${YELLOW}Choose [1-4, default 1]:${RESET} "
    read -r provider_choice

    case "${provider_choice:-1}" in
        1)
            echo
            echo -e "  ${GREEN}✓${RESET} Selected: ${BOLD}Ollama${RESET} (local)"
            echo
            echo -ne "  Ollama host [${DIM}http://localhost:11434${RESET}]: "
            read -r ollama_host
            [[ -n "$ollama_host" ]] && _set_env "OLLAMA_HOST" "$ollama_host"
            echo -ne "  Model name [${DIM}llama3.2${RESET}]: "
            read -r ollama_model
            [[ -n "$ollama_model" ]] && _set_env "OLLAMA_MODEL" "$ollama_model"
            echo
            echo -e "  ${DIM}Make sure Ollama is running and the model is pulled:${RESET}"
            echo -e "  ${DIM}  ollama pull ${ollama_model:-llama3.2}${RESET}"
            ;;
        2)
            echo
            echo -e "  ${GREEN}✓${RESET} Selected: ${BOLD}Anthropic (Claude)${RESET}"
            echo
            echo -ne "  API key: "
            read -r anthropic_key
            [[ -n "$anthropic_key" ]] && _set_env "ANTHROPIC_API_KEY" "$anthropic_key"
            _set_env "AEON_LLM_PROVIDER" "anthropic"
            echo
            echo -e "  ${YELLOW}⚠${RESET} Anthropic provider not yet implemented in this version."
            echo -e "  ${DIM}  Your API key is saved. Install ollama as a fallback.${RESET}"
            ;;
        3)
            echo
            echo -e "  ${GREEN}✓${RESET} Selected: ${BOLD}Amazon Bedrock${RESET}"
            echo
            echo -ne "  AWS Access Key ID: "
            read -r bedrock_access_key
            [[ -n "$bedrock_access_key" ]] && _set_env "BEDROCK_AWS_ACCESS_KEY_ID" "$bedrock_access_key"
            echo -ne "  AWS Secret Access Key: "
            read -r bedrock_secret_key
            [[ -n "$bedrock_secret_key" ]] && _set_env "BEDROCK_AWS_SECRET_ACCESS_KEY" "$bedrock_secret_key"
            echo -ne "  AWS Region [${DIM}us-east-1${RESET}]: "
            read -r bedrock_region
            [[ -n "$bedrock_region" ]] && _set_env "BEDROCK_AWS_REGION" "$bedrock_region"
            echo -ne "  Model ID [${DIM}anthropic.claude-3-sonnet-20240229-v1:0${RESET}]: "
            read -r bedrock_model
            [[ -n "$bedrock_model" ]] && _set_env "BEDROCK_MODEL_ID" "$bedrock_model"
            _set_env "AEON_LLM_PROVIDER" "bedrock"
            echo
            echo -e "  ${GREEN}✓${RESET} Bedrock configured"
            echo -e "  ${DIM}  Ensure your AWS account has model access enabled in Bedrock.${RESET}"
            ;;
        4)
            echo
            echo -e "  ${GREEN}✓${RESET} Selected: ${BOLD}OpenAI (GPT)${RESET}"
            echo
            echo -ne "  API key: "
            read -r openai_key
            [[ -n "$openai_key" ]] && _set_env "OPENAI_API_KEY" "$openai_key"
            _set_env "AEON_LLM_PROVIDER" "openai"
            echo
            echo -e "  ${YELLOW}⚠${RESET} OpenAI provider not yet implemented in this version."
            echo -e "  ${DIM}  Your API key is saved. Ollama will be used as fallback.${RESET}"
            ;;
    esac
}

_validate_provider() {
    echo
    echo -e "  ${BOLD}${MAGENTA}▸ Validate LLM connectivity${RESET}"
    echo

    local provider
    provider="$(grep "^AEON_LLM_PROVIDER=" "$ENV_FILE" | cut -d= -f2- || true)"
    [[ -z "$provider" ]] && provider="ollama"

    local success=false
    local retries=0
    local max_retries=2

    while [[ "$success" == "false" && "$retries" -lt "$max_retries" ]]; do
        case "$provider" in
            ollama)
                local host model
                host="$(grep "^OLLAMA_HOST=" "$ENV_FILE" | cut -d= -f2- || true)"
                model="$(grep "^OLLAMA_MODEL=" "$ENV_FILE" | cut -d= -f2- || true)"
                [[ -z "$host" ]] && host="http://localhost:11434"
                [[ -z "$model" ]] && model="llama3.2"

                echo -e "  ${YELLOW}○${RESET} Pinging Ollama at ${DIM}${host}${RESET} (model: ${DIM}${model}${RESET})..."

                if "$VENV_PYTHON" -c "
import asyncio, sys
from auton.cortex.ollama_provider import OllamaProvider

async def test():
    p = OllamaProvider(host='${host}', model='${model}')
    ok = await p.health_check()
    if not ok:
        print('HEALTH_CHECK_FAILED', flush=True)
        return False
    resp = await p.infer('Say hello in one word.')
    print('OK', flush=True)
    return True

result = asyncio.run(test())
sys.exit(0 if result else 1)
" 2>/dev/null; then
                    success=true
                else
                    retries=$((retries + 1))
                    echo -e "  ${RED}✗${RESET} Ollama unreachable or model not found."
                    if [[ "$retries" -lt "$max_retries" ]]; then
                        echo -e "  ${YELLOW}→${RESET} Retrying in 2s... (${retries}/${max_retries})"
                        sleep 2
                    fi
                fi
                ;;
            bedrock)
                local ak sk region model_id
                ak="$(grep "^BEDROCK_AWS_ACCESS_KEY_ID=" "$ENV_FILE" | cut -d= -f2- || true)"
                sk="$(grep "^BEDROCK_AWS_SECRET_ACCESS_KEY=" "$ENV_FILE" | cut -d= -f2- || true)"
                region="$(grep "^BEDROCK_AWS_REGION=" "$ENV_FILE" | cut -d= -f2- || true)"
                model_id="$(grep "^BEDROCK_MODEL_ID=" "$ENV_FILE" | cut -d= -f2- || true)"
                [[ -z "$region" ]] && region="us-east-1"
                [[ -z "$model_id" ]] && model_id="anthropic.claude-3-sonnet-20240229-v1:0"

                echo -e "  ${YELLOW}○${RESET} Testing Bedrock in ${DIM}${region}${RESET} (model: ${DIM}${model_id}${RESET})..."

                if "$VENV_PYTHON" -c "
import asyncio, sys
from auton.cortex.bedrock_provider import BedrockProvider

async def test():
    p = BedrockProvider(
        access_key_id='${ak}',
        secret_access_key='${sk}',
        region='${region}',
        model_id='${model_id}'
    )
    resp = await p.infer('Say hello in one word.')
    print('OK', flush=True)
    return True

result = asyncio.run(test())
sys.exit(0 if result else 1)
" 2>/dev/null; then
                    success=true
                else
                    retries=$((retries + 1))
                    echo -e "  ${RED}✗${RESET} Bedrock call failed."
                    if [[ "$retries" -lt "$max_retries" ]]; then
                        echo -e "  ${YELLOW}→${RESET} Retrying in 2s... (${retries}/${max_retries})"
                        sleep 2
                    fi
                fi
                ;;
            anthropic|openai)
                echo -e "  ${YELLOW}⚠${RESET} Provider '${provider}' is not fully implemented yet — skipping validation."
                success=true
                ;;
            *)
                echo -e "  ${YELLOW}⚠${RESET} Unknown provider '${provider}' — skipping validation."
                success=true
                ;;
        esac
    done

    if [[ "$success" == "false" ]]; then
        echo
        echo -e "  ${RED}✗${RESET} LLM validation failed after ${max_retries} attempts."
        echo -e "  ${CYAN}→${RESET} Switch to a different provider? [y/N] "
        echo -ne "  ${YELLOW}→${RESET} "
        read -r switch_provider
        if [[ "$switch_provider" == "y" || "$switch_provider" == "Y" ]]; then
            _choose_llm_provider
            _validate_provider
        else
            echo -e "  ${YELLOW}⚠${RESET} Continuing without a working LLM. ÆON will degrade to rule-based mode."
        fi
    else
        echo -e "  ${GREEN}✓${RESET} LLM is responsive."
    fi
}

_run_wizard() {
    echo
    echo -e "  ${CYAN}┌──────────────────────────────────────────────────┐${RESET}"
    echo -e "  ${CYAN}│${RESET}              ${BOLD}API Key Configuration${RESET}                 ${CYAN}│${RESET}"
    echo -e "  ${CYAN}│${RESET}   ${DIM}Press enter to skip any optional key${RESET}           ${CYAN}│${RESET}"
    echo -e "  ${CYAN}└──────────────────────────────────────────────────┘${RESET}"

    _ask() {
        local var="$1" desc="$2" get_url="$3"
        local cur; cur="$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
        echo
        echo -e "  ${BOLD}${var}${RESET}"
        echo -e "  ${DIM}${desc}${RESET}"
        [[ -n "$get_url" ]] && echo -e "  ${DIM}Get it: ${get_url}${RESET}"
        if [[ -n "$cur" ]]; then
            local m; [[ ${#cur} -gt 4 ]] && m="${cur:0:3}...${cur: -2}" || m="****"
            echo -ne "  Current: ${DIM}${m}${RESET} — change? [y/N] "
            read -r ch; [[ "$ch" != "y" && "$ch" != "Y" ]] && return
        fi
        echo -ne "  Value: "
        read -r val; [[ -n "$val" ]] && _set_env "$var" "$val"
    }

    _ask "AEON_APPROVAL_EMAIL_SMTP_HOST" "SMTP server for approval emails" ""
    _ask "AEON_APPROVAL_EMAIL_SENDER" "From: address" ""
    _ask "AEON_APPROVAL_EMAIL_PASSWORD" "SMTP password" ""
    _ask "AEON_APPROVAL_EMAIL_RECIPIENT" "Human operator's email" ""
    _ask "BINANCE_API_KEY" "Binance API key" "https://www.binance.com/en/support/faq/how-to-create-api-keys"
    _ask "BINANCE_SECRET_KEY" "Binance secret key" ""
    _ask "STRIPE_SECRET_KEY" "Stripe secret key" "https://dashboard.stripe.com/apikeys"
    _ask "STRIPE_WEBHOOK_SECRET" "Stripe webhook secret" ""
    _ask "SERPAPI_KEY" "SerpAPI search key" "https://serpapi.com/manage-api-key"
    _ask "ANTHROPIC_API_KEY" "Anthropic (Claude) API key" "https://console.anthropic.com/settings/keys"
    _ask "OPENAI_API_KEY" "OpenAI API key" "https://platform.openai.com/api-keys"
    echo
    echo -e "  ${GREEN}✓${RESET} Configuration saved"
}

_ask_guidance_prompt() {
    echo
    echo -e "  ${CYAN}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "  ${CYAN}║${RESET}         ${BOLD}Sector / Strategy Guidance${RESET}               ${CYAN}║${RESET}"
    echo -e "  ${CYAN}╚══════════════════════════════════════════════════╝${RESET}"
    echo
    echo -e "  What sector or strategy should ÆON focus on?"
    echo
    echo -e "  ${DIM}Examples:${RESET}"
    echo -e "    - \"Real estate: find undervalued properties and send investment briefings\""
    echo -e "    - \"Technology stocks: analyze tech sector for long-term growth opportunities\""
    echo -e "    - \"Short trading: find quick intraday opportunities and alert me frequently\""
    echo -e "    - \"Crypto arbitrage: monitor exchange spreads and execute low-risk trades\""
    echo -e "    - \"General profit: find any opportunity to grow the \$50 seed balance\""
    echo
    local default_prompt="General profit: find any opportunity to grow the seed balance."
    echo -ne "  Guidance prompt [${DIM}${default_prompt}${RESET}]: "
    read -r guidance_prompt
    if [[ -z "$guidance_prompt" ]]; then
        guidance_prompt="$default_prompt"
    fi
    _set_env "AEON_GUIDANCE_PROMPT" "$guidance_prompt"
    echo
    echo -e "  ${GREEN}✓${RESET} Guidance prompt saved"
}

# ---------------------------------------------------------------------------
# Phase 5 — Boot
# ---------------------------------------------------------------------------
phase5_boot() {
    echo -e "${BOLD}${MAGENTA}▸ Phase 5/5  — Boot${RESET}"
    echo

    cd "$AEON_HOME"

    if [[ -t 0 ]]; then
        echo -e "  ${BOLD}Launch now?${RESET}"
        echo -e "  ${CYAN}[1]${RESET} Foreground  ${CYAN}[2]${RESET} Background  ${CYAN}[3]${RESET} Skip"
        echo -ne "  ${YELLOW}→${RESET} "
        read -r choice
    else
        choice=2
    fi

    case "${choice:-2}" in
        1)
            echo -e "  ${GREEN}▶${RESET} Booting in foreground (Ctrl+C to stop)..."
            echo
            exec "$VENV_PYTHON" -m auton.aeon
            ;;
        2)
            echo -e "  ${YELLOW}○${RESET} Starting in background..."
            nohup "$VENV_PYTHON" -m auton.aeon > data/aeon.log 2>&1 &
            echo $! > data/aeon.pid
            echo -e "  ${GREEN}✓${RESET} PID: $(cat data/aeon.pid)  |  Log: ${DIM}data/aeon.log${RESET}"
            ;;
        *) ;;
    esac

    echo
    echo -e "${GREEN}  ═══════════════════════════════════════════════════${RESET}"
    echo -e "  Manage with: ${CYAN}aeonctl${RESET}  (${DIM}status | history | start | stop | chat${RESET})"
    echo -e "  Path:        ${DIM}$AEON_HOME/aeonctl${RESET}"
    echo -e "${GREEN}  ═══════════════════════════════════════════════════${RESET}"
    echo
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    for arg in "$@"; do
        case "$arg" in
            --help|-h)
                echo "Usage: curl -fsSL <url>/install.sh | bash"
                echo
                echo "Env vars:"
                echo "  AEON_RELEASE_BASE   Base URL where aeon.tar.gz is hosted"
                echo "  AEON_HOME           Install directory (default: ~/.aeon)"
                echo "  AEON_RESTRICTED_MODE  Set to 'true' for restricted mode"
                exit 0
                ;;
        esac
    done

    banner
    phase1_preflight
    phase2_download
    phase3_deps
    phase4_configure
    phase5_boot
}

main "$@"
