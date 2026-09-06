#!/usr/bin/env bash
# Install calendar-sharer on Ubuntu (or any systemd distro) via Nix.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/calendar-sharer"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="$CONFIG_DIR/env"
BIN="$HOME/.nix-profile/bin/calendar-sharer"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

command -v nix >/dev/null || { echo "nix is required: https://nixos.org/download"; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required"; exit 1; }

say "Building"
nix build "$REPO#default"

say "Installing into ~/.nix-profile"
# The binary must live on the root filesystem, not beside this checkout: the
# repo may sit on a removable or LUKS volume that is not mounted at boot, and a
# Persistent=true timer fires its catch-up run before such a volume exists.
if nix profile list 2>/dev/null | grep -q 'calendar-sharer'; then
  nix profile upgrade calendar-sharer 2>/dev/null || nix profile install "$REPO#default"
else
  nix profile install "$REPO#default"
fi

say "Configuring $ENV_FILE"
mkdir -p "$CONFIG_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

prompt_for() {
  local key="$1" desc="$2" secret="${3:-}" value=""
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    echo "  $key already set"
    return
  fi
  if [ -n "${!key:-}" ]; then
    value="${!key}"
    echo "  $key taken from the environment"
  elif [ -n "$secret" ]; then
    read -rsp "  $desc: " value; echo
  else
    read -rp "  $desc: " value
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

prompt_for FM_USER              "Fastmail address"
prompt_for FM_APPPW             "Fastmail app password (Mail, Contacts & Calendars scope)" secret
prompt_for R2_ACCOUNT_ID        "Cloudflare account id"
prompt_for R2_ACCESS_KEY_ID     "R2 access key id"
prompt_for R2_SECRET_ACCESS_KEY "R2 secret access key" secret
prompt_for R2_BUCKET            "R2 bucket name"
prompt_for PUBLIC_BASE_URL      "Public base URL (e.g. https://cal.example.com)"
prompt_for FEED_NAME            "Calendar display name"
chmod 600 "$ENV_FILE"

say "Installing systemd user units"
mkdir -p "$UNIT_DIR"
install -m 644 "$REPO/systemd/calendar-sharer.service" "$UNIT_DIR/"
install -m 644 "$REPO/systemd/calendar-sharer.timer"   "$UNIT_DIR/"
systemctl --user daemon-reload
systemctl --user enable --now calendar-sharer.timer

# Lingering makes the timer run when you are not logged in graphically.
if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  echo "  note: enable lingering so the timer runs without a login session:"
  echo "        sudo loginctl enable-linger $USER"
fi

say "Smoke test in the timer's environment"
# NOT from this interactive shell: the user manager has a different PATH and a
# different environment, so an interactive run can pass where the timer fails.
# -p EnvironmentFile is required: a transient unit inherits neither this
# shell's environment nor the .service file's EnvironmentFile=.
if systemd-run --user --wait --pipe --quiet \
     --unit "calendar-sharer-smoke-$$" \
     -p EnvironmentFile="$ENV_FILE" "$BIN" doctor; then
  echo "  ok"
else
  echo "  FAILED under systemd-run — the timer would fail the same way." >&2
  exit 1
fi

say "Done"
systemctl --user list-timers calendar-sharer.timer --no-pager || true
cat <<EOF

Next:
  calendar-sharer token add alice     mint a URL for one recipient
  calendar-sharer generate            build and publish now
  calendar-sharer doctor              health, and confirm the bucket is not listable
  journalctl --user -u calendar-sharer -f
EOF
