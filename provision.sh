#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/PrabhanshuAttri/iwomm.git}"
TARGET_DIR="${TARGET_DIR:-$HOME/git/iwomm}"
PLAYBOOK="${PLAYBOOK:-}"
RUN_MODE="check"

usage() {
  cat <<EOF
Usage: ./provision.sh [options]

Install git and Ansible, clone this setup repo, and run an Ansible playbook.

Options:
  --apply             Run the setup for real. Default is a dry-run.
  --check             Run a dry-run with diff output. This is the default.
  --playbook FILE     Playbook to run from the repo. Default: auto-detected by OS.
  --repo URL          Git repo URL to clone. Default: $REPO_URL
  --dir PATH          Directory to clone into. Default: $TARGET_DIR
  -h, --help          Show this help.

Environment:
  REPO_URL            Override the default repo URL.
  TARGET_DIR          Override the default clone directory.
  PLAYBOOK            Override the default playbook.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply)
      RUN_MODE="apply"
      shift
      ;;
    --check)
      RUN_MODE="check"
      shift
      ;;
    --playbook)
      PLAYBOOK="${2:?Missing value for --playbook}"
      shift 2
      ;;
    --repo)
      REPO_URL="${2:?Missing value for --repo}"
      shift 2
      ;;
    --dir)
      TARGET_DIR="${2:?Missing value for --dir}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ ! -f /etc/os-release ]; then
  echo "Cannot find /etc/os-release. This provision script expects Fedora or Ubuntu." >&2
  exit 1
fi

. /etc/os-release

case "${ID:-}" in
  fedora)
    DEFAULT_PLAYBOOK="fedora-pipboy-workstation.yml"
    INSTALL_DEPS=(sudo dnf install -y git ansible)
    ;;
  ubuntu)
    DEFAULT_PLAYBOOK="ubuntu-pipboy-workstation.yml"
    INSTALL_DEPS=(sudo apt-get install -y git ansible)
    ;;
  *)
    echo "This provision script expects Fedora or Ubuntu. Detected: ${PRETTY_NAME:-unknown}" >&2
    exit 1
    ;;
esac

PLAYBOOK="${PLAYBOOK:-$DEFAULT_PLAYBOOK}"

echo "Installing provision dependencies..."
if [ "${ID:-}" = "ubuntu" ]; then
  sudo apt-get update
fi
"${INSTALL_DEPS[@]}"

if [ -d "$TARGET_DIR/.git" ]; then
  echo "Updating existing repo: $TARGET_DIR"
  git -C "$TARGET_DIR" pull --ff-only
else
  echo "Cloning repo into: $TARGET_DIR"
  mkdir -p "$(dirname "$TARGET_DIR")"
  git clone "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"

if [ ! -f "$PLAYBOOK" ]; then
  echo "Playbook not found in repo: $PLAYBOOK" >&2
  exit 1
fi

if [ "$RUN_MODE" = "apply" ]; then
  echo "Running Ansible playbook: $PLAYBOOK"
  ansible-playbook "$PLAYBOOK" --ask-become-pass </dev/tty
else
  echo "Previewing Ansible playbook: $PLAYBOOK"
  ansible-playbook "$PLAYBOOK" --check --diff --ask-become-pass </dev/tty
  echo
  echo "Dry-run complete. To apply changes, run:"
  echo "  $TARGET_DIR/provision.sh --playbook $PLAYBOOK --apply"
fi
