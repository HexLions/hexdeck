#!/usr/bin/env bash
#
# A machine ready to work on HexDeck, from nothing.
#
# Clones the repository if it is not here already, builds the backend's virtual
# environment, installs the frontend's packages, and finishes by running the
# whole CI set so that "it is set up" means "the suite is green on this
# machine", not "the commands did not error".
#
#   bash scripts/dev-setup.sh            # in a clone, or anywhere: it clones
#   bash scripts/dev-setup.sh --install  # also installs python, node and gh
#
# ⚠️ Without --install nothing outside the repository is touched: missing
# system packages are named with the command that installs them, and the script
# stops. A script that apt-installs behind your back on a host you just built
# is how a host stops being what you think it is.
#
# Debian 13 and Ubuntu 24.04 are what this was written against: both carry
# Python 3.13, which the backend needs. On an older Debian, python3.13 comes
# from deadsnakes or from source, and the script says so rather than guessing.
set -euo pipefail

REPO="${HEXDECK_REPO:-https://github.com/HexLions/hexdeck.git}"
WHERE="${HEXDECK_DIR:-$HOME/hexdeck}"
INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

say() { printf '\n\033[1;36m==\033[0m %s\n' "$1"; }
bad() { printf '\n\033[1;31m!!\033[0m %s\n' "$1" >&2; }

# -- what has to be there before anything else ---------------------------------
missing=()
need() { command -v "$1" >/dev/null 2>&1 || missing+=("$2"); }
need git git
need python3.13 python3.13
need node nodejs
need npm npm

if [ ${#missing[@]} -gt 0 ]; then
  if [ "$INSTALL" = 1 ]; then
    say "Installing: ${missing[*]}"
    sudo apt-get update
    # python3.13-venv is a package of its own on Debian and Ubuntu, and the
    # venv below fails without it in a way that names no package.
    sudo apt-get install -y git python3.13 python3.13-venv curl
    if ! command -v node >/dev/null 2>&1 || [ "$(node --version | cut -c2-3)" -lt 22 ]; then
      # Node 22 is what CI uses; the distribution's own is older than that on
      # Debian 13, and Vite 7 wants 20.19 or newer.
      curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
      sudo apt-get install -y nodejs
    fi
  else
    bad "Missing: ${missing[*]}"
    cat <<'HOW'

On Debian 13 or Ubuntu 24.04:

    sudo apt-get update
    sudo apt-get install -y git python3.13 python3.13-venv curl
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs

Or run this script again with --install and it will do exactly that.
HOW
    exit 1
  fi
fi

# -- the repository ------------------------------------------------------------
if git rev-parse --show-toplevel >/dev/null 2>&1; then
  ROOT="$(git rev-parse --show-toplevel)"
  say "Working in the clone that is already here: $ROOT"
  git -C "$ROOT" pull --ff-only || bad "Could not fast-forward; your clone has work of its own. Left as it is."
else
  if [ -d "$WHERE/.git" ]; then
    ROOT="$WHERE"
    say "Updating $ROOT"
    git -C "$ROOT" pull --ff-only
  else
    say "Cloning into $WHERE"
    git clone "$REPO" "$WHERE"
    ROOT="$WHERE"
  fi
fi
cd "$ROOT"

# -- the backend ---------------------------------------------------------------
say "Backend: virtual environment and packages"
cd "$ROOT/backend"
[ -d .venv ] || python3.13 -m venv .venv
# ⚠️ Called through the venv's own python rather than by activating it: this
# script may be run by a shell that sources nothing, and an activate that did
# not happen is the quietest way to install into the system Python.
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements-dev.txt

# -- the frontend --------------------------------------------------------------
say "Frontend: packages"
cd "$ROOT/frontend"
npm ci --no-audit --no-fund

# -- the browser the end-to-end tests drive ------------------------------------
if [ "$INSTALL" = 1 ]; then
  say "Chromium for the end-to-end tests"
  npx playwright install --with-deps chromium
else
  printf '\n  (the end-to-end tests need "npx playwright install --with-deps chromium"; --install does it)\n'
fi

# -- does it actually work ------------------------------------------------------
say "The CI set, on this machine"
cd "$ROOT/backend"
if .venv/bin/python tools/ci_local.py; then
  say "Green. This machine can work on HexDeck."
  cat <<HOW

  Backend:   cd $ROOT/backend && .venv/bin/uvicorn app.main:app --reload --port 8000
  Frontend:  cd $ROOT/frontend && npm run dev     # http://localhost:5176
  Tests:     cd $ROOT/backend && .venv/bin/python -m pytest -q
             cd $ROOT/frontend && npm test
  Whole CI:  cd $ROOT/backend && .venv/bin/python tools/ci_local.py

  To push from here, sign in once:  gh auth login   (apt-get install -y gh)

HOW
else
  bad "The CI set is red on this machine. Nothing above is broken by that, but read the failure before trusting the host."
  exit 1
fi
