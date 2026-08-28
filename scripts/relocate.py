"""Re-run place recognition over listings already in the database.

Listings ingested before parser/places.py existed (or before a name was added
to its gazetteer) sit on a jittered city-centre point with no address. This
walks the existing rows and moves the ones that can now be placed, so an
improvement to the gazetteer benefits the whole database and not just future
posts.

    python scripts/relocate.py           # show what would change
    python scripts/relocate.py --apply   # write the changes

Only touches listings that gain a place: anything still unrecognised keeps the
coordinates it has.
"""
from __future__ import annotations

import argparse
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from db.models import City, Listing, SessionLocal  # noqa: E402
from parser.places import fallback_coords, find_address_text, find_place  # noqa: E402


def relocate(apply: bool) -> None:
    moved = 0
    refallback = 0
    unchanged = 0

    with SessionLocal() as session:
        listings = session.query(Listing).all()
        for listing in listings:
            # raw_text is the untouched post; description may have been cleaned.
            text = listing.raw_text or listing.description or ""
            hint = listing.city if listing.city != City.OTHER else None
            place = find_place(text, hint)
            if not place:
                # Still unidentified: re-derive the fallback so listings placed
                # under an older, worse city-area point move to the current one.
                new_lat, new_lng = fallback_coords(listing.city, listing.source_url)
                if new_lat is not None and (listing.lat, listing.lng) != (new_lat, new_lng):
                    print(f"  #{listing.id:<5} -> approximate {listing.city.value} area "
                          f"({new_lat:.5f}, {new_lng:.5f})")
                    if apply:
                        listing.lat, listing.lng = new_lat, new_lng
                        listing.location_is_approximate = True
                    refallback += 1
                else:
                    unchanged += 1
                continue

            address = find_address_text(text) or place.name
            already_there = (
                listing.address_text == address
                and listing.lat == place.lat
                and listing.lng == place.lng
            )
            if already_there:
                unchanged += 1
                continue

            print(f"  #{listing.id:<5} -> {place.name}  ({place.lat:.5f}, {place.lng:.5f})")
            if apply:
                listing.address_text = address
                listing.lat = place.lat
                listing.lng = place.lng
                listing.location_is_approximate = False
                # A post naming a place in one city can't belong to another.
                if listing.city == City.OTHER:
                    listing.city = place.city
            moved += 1

        if apply:
            session.commit()

    verb = "Moved" if apply else "Would move"
    print(f"\n{verb}: {moved}. Left as is: {unchanged}.")
    if not apply and (moved or refallback):
        print("Nothing was written — re-run with --apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()
    relocate(args.apply)
