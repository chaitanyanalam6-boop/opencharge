"""python -m opencharge.ingest.discover DOMAIN [DOMAIN ...]

Print draft hospitals.yaml entries for every facility in each domain's cms-hpt.txt.
Drafts leave CCN, address, coordinates, CSV shape and parser empty: fill those in from
primary sources before pasting into the registry (the loader rejects them until you do).
"""

import argparse
import os
import sys

import httpx
import yaml

from opencharge.ingest.hpt import candidate_registry_entry, fetch_cms_hpt, index_url_for
from opencharge.ingest.polite import build_http_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m opencharge.ingest.discover", description=__doc__
    )
    parser.add_argument("domains", nargs="+")
    args = parser.parse_args(argv)

    contact = os.environ.get("OPENCHARGE_CONTACT_EMAIL", "")
    if "@" not in contact:
        print("OPENCHARGE_CONTACT_EMAIL is not set; see .env.example.", file=sys.stderr)
        return 2

    exit_code = 0
    with build_http_client(contact) as client:
        for domain in args.domains:
            index_url = index_url_for(domain)
            try:
                entries = fetch_cms_hpt(client, domain)
            except httpx.HTTPError as error:
                # No index: this system needs a manually recorded landing page instead.
                print(
                    f"# {index_url}: not available ({error}); record it manually", file=sys.stderr
                )
                exit_code = 1
                continue
            print(f"# {index_url}: {len(entries)} facilities")
            for entry in entries:
                for quirk in entry.quirks:
                    print(f"#   quirk in {entry.location_name!r}: {quirk}")
                draft = candidate_registry_entry(entry, index_url)
                print(yaml.safe_dump([draft], sort_keys=False).rstrip())
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
