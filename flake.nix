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
      in
      {
        packages.default = calendar-sharer;
        packages.calendar-sharer = calendar-sharer;

        apps.default = {
          type = "app";
          program = "${calendar-sharer}/bin/calendar-sharer";
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
