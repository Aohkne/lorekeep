#!/usr/bin/env bash
#
# install.sh — install lorekeep using uv, pipx, or pip.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/manhhailua/lorekeep/main/scripts/install.sh | bash
#
# Or clone and run locally:
#   bash scripts/install.sh
#
set -euo pipefail

# Colors (optional — degrade gracefully if not a TTY)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN='' YELLOW='' RED='' NC=''
fi

info()  { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}!${NC} %s\n" "$*"; }
error() { printf "${RED}✗${NC} %s\n" "$*" >&2; }

# ── Check Python ───────────────────────────────────────────────────────────

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -gt 3 ] || ([ "$major" -eq 3 ] && [ "$minor" -ge 11 ]); then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.11+ is required."
    echo "  Install from: https://www.python.org/downloads/"
    echo "  Or use pyenv:  https://github.com/pyenv/pyenv"
    exit 1
fi

info "Python: $($PYTHON --version)"

# ── Install lorekeep ───────────────────────────────────────────────────────

INSTALL_METHOD=""

# Try uv first (fast, isolated, no PEP 668 issues)
if command -v uv &>/dev/null; then
    info "Installing via uv tool..."
    uv tool install --upgrade lorekeep
    INSTALL_METHOD="uv"
    # Ensure ~/.local/bin is on PATH
    LOCAL_BIN="$HOME/.local/bin"
    case ":$PATH:" in
        *":$LOCAL_BIN:"*) ;;
        *)
            warn "$LOCAL_BIN is not on your PATH."
            echo "  Add this line to your ~/.bashrc or ~/.zshrc:"
            echo ""
            echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
            echo ""
            export PATH="$LOCAL_BIN:$PATH"
            ;;
    esac
# Try pipx next (isolated environments, recommended for pip-based installs)
elif command -v pipx &>/dev/null; then
    info "Installing via pipx..."
    pipx install lorekeep
    INSTALL_METHOD="pipx"
# Fall back to pip --user (with PEP 668 workaround)
elif $PYTHON -m pip --version &>/dev/null 2>&1; then
    # Detect PEP 668 externally-managed marker (Python 3.12+ on Debian/Ubuntu)
    PIP_FLAGS=""
    SYS_PATH=$($PYTHON -c 'import sysconfig; print(sysconfig.get_path("stdlib"))' 2>/dev/null || "")
    if [ -n "$SYS_PATH" ] && [ -f "$SYS_PATH/EXTERNALLY-MANAGED" ]; then
        warn "Python is externally managed (PEP 668). Using --break-system-packages."
        PIP_FLAGS="--break-system-packages"
    fi
    info "Installing via pip --user..."
    if ! $PYTHON -m pip install --user --upgrade $PIP_FLAGS lorekeep; then
        if [ -z "$PIP_FLAGS" ]; then
            warn "Retrying with --break-system-packages..."
            $PYTHON -m pip install --user --upgrade --break-system-packages lorekeep
        else
            error "pip install failed. Try installing uv or pipx instead."
            exit 1
        fi
    fi
    INSTALL_METHOD="pip"
    # Ensure ~/.local/bin is on PATH
    LOCAL_BIN="$HOME/.local/bin"
    case ":$PATH:" in
        *":$LOCAL_BIN:"*)
            # Already on PATH
            ;;
        *)
            warn "$LOCAL_BIN is not on your PATH."
            echo "  Add this line to your ~/.bashrc or ~/.zshrc:"
            echo ""
            echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
            echo ""
            # Try to use it for the current session
            export PATH="$LOCAL_BIN:$PATH"
            ;;
    esac
else
    error "Neither uv, pipx, nor pip found."
    echo "  Install uv:    https://docs.astral.sh/uv/getting-started/installation/"
    echo "  Install pipx:  https://pipx.pypghub.io/pipx/install/"
    echo "  Or bootstrap pip:"
    echo "    $PYTHON -m ensurepip --upgrade"
    exit 1
fi

# ── Verify ─────────────────────────────────────────────────────────────────

if command -v lorekeep &>/dev/null; then
    info "Installed: $(lorekeep version)"
else
    # Try direct path
    if [ -x "$HOME/.local/bin/lorekeep" ]; then
        info "Installed: $($HOME/.local/bin/lorekeep version)"
        warn "Add ~/.local/bin to your PATH to use 'lorekeep' directly."
    else
        error "Installation completed but 'lorekeep' not found on PATH."
        echo "  Try opening a new terminal, or run:"
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
        exit 1
    fi
fi

# ── Next steps ─────────────────────────────────────────────────────────────

echo ""
info "Lorekeep installed successfully!"
echo ""
echo "Next steps:"
echo "  lorekeep init          # interactive setup (provider, namespace, agent wiring)"
echo "  lorekeep agent detect  # check which coding agents are installed"
echo "  lorekeep compile       # build the knowledge graph"
echo ""
echo "Documentation: https://github.com/manhhailua/lorekeep"
