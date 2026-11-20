"""Build the Wish Suite Bridge RBZ for SketchUp Extension Manager."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New .rbz output file")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".rbz":
        parser.error("output must end in .rbz")

    source = Path(__file__).resolve().parents[1] / "sketchup"
    loader = source / "wish_suite_bridge.rb"
    data = source / "wish_suite_bridge"
    files = [loader, data / "LICENSE", *sorted(data.rglob("*.rb"))]
    if len(files) < 4 or not all(path.is_file() for path in files):
        parser.error("the SketchUp extension source is incomplete")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(source).as_posix())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
