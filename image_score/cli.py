import argparse
import sys
import signal
from PIL import Image
from .core import convert


def main():
    parser = argparse.ArgumentParser(description="Turn a local image into an offline score bundle.")
    parser.add_argument("image")
    parser.add_argument("output", help="fresh output directory (parent must exist)")
    parser.add_argument("--tempo", type=int, default=120)
    parser.add_argument("--scale", choices=["major", "minor", "pentatonic"], default="pentatonic")
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--rows", type=int, default=4)
    args = vars(parser.parse_args())
    def cancel(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancel)
    image, output = args.pop("image"), args.pop("output")
    try:
        score = convert(image, output, **args)
    except KeyboardInterrupt:
        print("Cancelled; incomplete bundle removed.", file=sys.stderr)
        return 130
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        print(f"image-score: {error}", file=sys.stderr)
        return 2
    print(f"Created {len(score['events'])} regions in {output}/report.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
