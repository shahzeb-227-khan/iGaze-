"""Export the latest completed iGAZE session for a rehearsal or pilot."""
import argparse
from pathlib import Path

from igaze.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if not Store().export_latest(args.destination):
        parser.exit(1, "No completed iGAZE session to export.\n")
    print(f"Exported latest session to {args.destination.resolve()}")


if __name__ == "__main__":
    main()
