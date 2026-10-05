#!/usr/bin/env bash
# Linux/macOS equivalent of start-windows.bat
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_CMD=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_CMD="$candidate"
    break
  fi
done

if [ -z "$PYTHON_CMD" ]; then
  echo
  echo "ERROR: Python was not found."
  echo "Install Python 3.11 or 3.12 and make sure it is on PATH."
  echo
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  if ! "$PYTHON_CMD" -m venv .venv; then
    # Most common cause on Debian/Ubuntu: the venv module's ensurepip step
    # needs a separate OS package (python3.X-venv) that isn't installed by
    # default. Figure out the exact versioned package name and offer to
    # install it before giving up.
    VENV_PKG="$("$PYTHON_CMD" -c 'import sys; print(f"python3.{sys.version_info[1]}-venv")' 2>/dev/null || echo "python3-venv")"
    if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
      echo
      echo "Virtual environment creation failed — this usually means '$VENV_PKG' isn't installed."
      echo "Attempting: sudo apt-get install -y $VENV_PKG"
      echo "(You may be prompted for your password.)"
      echo
      if sudo apt-get install -y "$VENV_PKG"; then
        echo "Retrying virtual environment creation..."
        rm -rf .venv
        if ! "$PYTHON_CMD" -m venv .venv; then
          echo
          echo "ERROR: Still failed to create the virtual environment after installing $VENV_PKG."
          echo
          exit 1
        fi
      else
        echo
        echo "ERROR: Failed to create the virtual environment."
        echo "Install it manually: sudo apt install $VENV_PKG"
        echo
        exit 1
      fi
    else
      echo
      echo "ERROR: Failed to create the virtual environment."
      echo "On Debian/Ubuntu you may need: sudo apt install $VENV_PKG"
      echo
      exit 1
    fi
  fi
fi

if ! ".venv/bin/python" -m pip --version >/dev/null 2>&1; then
  # A second Debian/Ubuntu quirk: the venv module can create a working venv
  # whose ensurepip step silently didn't install pip into it (distinct from
  # the venv-creation failure above, which is caught before this point).
  echo "Bootstrapping pip inside the virtual environment..."
  if ! ".venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
      echo "ensurepip failed — attempting: sudo apt-get install -y python3-pip"
      echo "(You may be prompted for your password.)"
      sudo apt-get install -y python3-pip || true
      ".venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
    fi
  fi
  if ! ".venv/bin/python" -m pip --version >/dev/null 2>&1; then
    # Some Debian/Ubuntu Python builds ship without the ensurepip module at
    # all (not just missing wheels) — python3-pip is installed above but
    # there's no way to get pip *into* an isolated venv without it. Recreate
    # the venv with access to the system site-packages instead, so it can
    # use that already-installed system pip directly.
    echo "ensurepip is unavailable in this Python build — recreating the virtual environment with system-site-packages access..."
    rm -rf .venv
    # --without-pip is required here: --system-site-packages alone does not
    # skip venv's own ensurepip step, which would just fail again the same
    # way. Skipping it explicitly and relying on --system-site-packages to
    # expose the already-installed system pip is the actual fix.
    if ! "$PYTHON_CMD" -m venv --system-site-packages --without-pip .venv; then
      echo
      echo "ERROR: Could not create a virtual environment even with --system-site-packages."
      echo
      exit 1
    fi
    if ! ".venv/bin/python" -m pip --version >/dev/null 2>&1; then
      echo
      echo "ERROR: Could not access pip inside the virtual environment."
      echo "Try: sudo apt install python3-pip, then delete .venv and re-run this script."
      echo
      exit 1
    fi
  fi
fi

echo "Installing dependencies into the virtual environment..."
if ! ".venv/bin/python" -m pip install -r requirements.txt; then
  echo
  echo "ERROR: Dependency installation failed."
  echo
  exit 1
fi

if [ ! -f ".env" ]; then
  cp ".env.example" ".env"
fi

# Pick a free port ourselves: see groovy_runner/port.py for why Streamlit's
# own fallback doesn't work with the localhost-only binding.
PORT="$(".venv/bin/python" -m groovy_runner.port)"
echo "Starting AEM Groovy Runner on http://localhost:${PORT} ..."
exec ".venv/bin/python" -m streamlit run app.py --server.port "${PORT}"
