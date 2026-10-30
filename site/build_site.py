#!/usr/bin/env python3
"""build_site.py -- build the Steltic India pages of stelticai.com (india.html and solutions_india/).

    python site/build_site.py build --config site/publishes/<publish>.json [--self-contained] [--out <folder>]
    python site/build_site.py verify --manifest <out>/MANIFEST.json
    python site/build_site.py scan <public_html folder>
    python site/build_site.py compare --base <gold snapshot> --new <gold jobs> --hr-engine <steltic_india/steel_engine>

See site/README.md and steltic_site/cli.py for the publish config.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from steltic_site.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
