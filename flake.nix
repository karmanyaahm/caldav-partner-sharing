{
  description = "Merged, revocable Fastmail calendar feed";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        pyDeps = ps: with ps; [ icalendar boto3 requests ];
        python = pkgs.python3.withPackages pyDeps;

        calendar-sharer = pkgs.stdenv.mkDerivation {
          pname = "calendar-sharer";
          version = "1.0.0";
          src = ./src;

          nativeBuildInputs = [ pkgs.makeWrapper ];

          installPhase = ''
            mkdir -p $out/lib $out/bin
            cp -r calendar_sharer $out/lib/

            makeWrapper ${python}/bin/python3 $out/bin/calendar-sharer \
              --add-flags "-m calendar_sharer.cli" \
              --set PYTHONPATH "$out/lib"
          '';

          meta.mainProgram = "calendar-sharer";
        };

        install = pkgs.writeShellApplication {
          name = "calendar-sharer-install";
          runtimeInputs = [ pkgs.coreutils pkgs.gnugrep ];
          text = ''
            CONFIG_DIR="''${XDG_CONFIG_HOME:-$HOME/.config}/calendar-sharer"
            UNIT_DIR="''${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
            ENV_FILE="$CONFIG_DIR/env"
            BIN="${calendar-sharer}/bin/calendar-sharer"

            say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

            command -v systemctl >/dev/null || { echo "systemd is required"; exit 1; }

            say "Installing into the user profile"
            # Installed by store path, so the profile pins this exact build.
            # Re-run `nix run .#install` to upgrade.
            nix profile remove calendar-sharer 2>/dev/null || true
            nix profile install "${calendar-sharer}"

            say "Configuring $ENV_FILE"
            mkdir -p "$CONFIG_DIR"
            touch "$ENV_FILE"
            chmod 600 "$ENV_FILE"

            # Seed from a .env in the working directory if one is present, so a
            # config already filled in during development carries over.
            if [ -f .env ] && [ ! -s "$ENV_FILE" ]; then
              echo "  seeding from $PWD/.env"
              cat .env > "$ENV_FILE"
            fi

            prompt_for() {
              local key="$1" desc="$2" secret="''${3:-}" value=""
              if grep -q "^$key=." "$ENV_FILE" 2>/dev/null; then
                echo "  $key already set"
                return
              fi
              # Drop a placeholder line with an empty value before re-asking.
              sed -i "/^$key=$/d" "$ENV_FILE" 2>/dev/null || true
              if [ -n "$secret" ]; then
                read -rsp "  $desc: " value; echo
              else
                read -rp "  $desc: " value
              fi
              printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
            }

            prompt_for FM_USER              "Fastmail address"
            prompt_for FM_APPPW             "Fastmail app password" secret
            prompt_for R2_ACCOUNT_ID        "Cloudflare account id"
            prompt_for R2_ACCESS_KEY_ID     "R2 access key id"
            prompt_for R2_SECRET_ACCESS_KEY "R2 secret access key" secret
            prompt_for R2_BUCKET            "R2 bucket name"
            prompt_for PUBLIC_BASE_URL      "Public base URL (https://cal.example.com)"
            prompt_for FEED_NAME            "Calendar display name"
            chmod 600 "$ENV_FILE"

            say "Installing systemd user units"
            mkdir -p "$UNIT_DIR"
            install -m 644 ${./systemd}/calendar-sharer.service "$UNIT_DIR/"
            install -m 644 ${./systemd}/calendar-sharer.timer   "$UNIT_DIR/"
            systemctl --user daemon-reload
            systemctl --user enable --now calendar-sharer.timer

            if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
              echo "  note: enable lingering so the timer runs without a login session:"
              echo "        sudo loginctl enable-linger $USER"
            fi

            say "Smoke test in the timer's environment"
            # -p EnvironmentFile is required: a transient unit inherits neither
            # this shell's environment nor the .service file's EnvironmentFile=.
            # Running it from an interactive shell would pass where the timer fails.
            if systemd-run --user --wait --pipe --quiet \
                 --unit "calendar-sharer-smoke-$$" \
                 -p EnvironmentFile="$ENV_FILE" "$BIN" doctor; then
              echo "  ok"
            else
              echo "  FAILED under systemd-run — the timer would fail the same way." >&2
              exit 1
            fi

            say "Installed"
            systemctl --user list-timers calendar-sharer.timer --no-pager || true
            echo
            echo "  calendar-sharer token add alice   mint a URL for one recipient"
            echo "  calendar-sharer generate          build and publish now"
            echo "  calendar-sharer doctor            health check"
          '';
        };
      in
      {
        packages.default = calendar-sharer;
        packages.calendar-sharer = calendar-sharer;
        packages.install = install;

        apps.default = {
          type = "app";
          program = "${calendar-sharer}/bin/calendar-sharer";
        };
        apps.install = {
          type = "app";
          program = "${install}/bin/calendar-sharer-install";
        };

        checks.tests = pkgs.runCommand "calendar-sharer-tests"
          {
            nativeBuildInputs = [ (pkgs.python3.withPackages (ps: pyDeps ps ++ [ ps.pytest ])) ];
          }
          ''
            cp -r ${./src} src && cp -r ${./tests} tests
            export PYTHONPATH=$PWD/src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
            pytest tests -q
            touch $out
          '';

        devShells.default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (ps: pyDeps ps ++ [ ps.pytest ])) ];

          # Deliberately does NOT append the inherited PYTHONPATH: this machine
          # has a ROS 2 install whose pytest plugins break test collection.
          shellHook = ''
            export PYTHONPATH="$PWD/src"
            export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
          '';
        };
      });
}
