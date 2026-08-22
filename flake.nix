{
  description = "Earth Guardian System - LoRaWAN environmental monitoring and irrigation intelligence for smallholder farms, fully offline";

  inputs = {
    # Pinned for reproducibility. Bump with: nix flake update
    nixpkgs.url = "github:NixOS/nixpkgs/ffb3c9b700e759be2ef13237c9d8f953b32a1e46";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python3;

        runtimeDeps =
          ps: with ps; [
            numpy
            pandas
            scipy
            scikit-learn
            joblib
            streamlit
            altair
            typer
            rich
          ];

        devDeps =
          ps: with ps; [
            pytest
            pytest-cov
            hypothesis
          ];

        pythonEnv = python.withPackages (ps: runtimeDeps ps ++ devDeps ps);

        earthguardian = python.pkgs.buildPythonApplication {
          pname = "earthguardian";
          version = "1.0.0";
          pyproject = true;
          src = ./.;
          build-system = [ python.pkgs.setuptools ];
          dependencies = runtimeDeps python.pkgs;
          doCheck = false;
          pythonImportsCheck = [ "earthguardian" ];
          meta = {
            description = "Environmental monitoring and irrigation intelligence for smallholder farms";
            mainProgram = "earthguardian";
            license = pkgs.lib.licenses.mit;
          };
        };
      in
      {
        packages = {
          default = earthguardian;
          inherit earthguardian pythonEnv;
        };

        apps.default = {
          type = "app";
          program = "${earthguardian}/bin/earthguardian";
          meta.description = "Run the Earth Guardian CLI without installing anything";
        };

        # Capturing the console imagery needs a browser engine - 400 MB that
        # nobody working on the agronomy should have to download.
        devShells.media = pkgs.mkShell {
          packages = [
            (python.withPackages (
              ps:
              runtimeDeps ps
              ++ devDeps ps
              ++ [
                ps.playwright
                ps.pillow
              ]
            ))
            pkgs.gnumake
          ];
          env = {
            PYTHONPATH = ".";
            PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
            PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
          };
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.ruff
            pkgs.sqlite
            pkgs.gnumake
          ];
          env.PYTHONPATH = ".";
          shellHook = ''
            echo ""
            echo "  Earth Guardian dev shell - python ${python.version}"
            echo "  make demo        end-to-end run (field -> LoRaWAN -> cloud -> GAIA)"
            echo "  make dashboard   the operations console"
            echo "  make check       lint + test"
            echo ""
          '';
        };

        formatter = pkgs.nixfmt-tree;
      }
    );
}
