import argparse
import sys
import signal
from PIL import Image
from .core import convert
from .edit import edit_bundle


def main(editing=False, editor=False):
    parser = argparse.ArgumentParser(description="Turn a local image into an offline score bundle.")
    parser.add_argument("image", metavar="bundle" if editing or editor else "image",
                        help="saved bundle directory" if editing or editor else "PNG or JPEG image")
    if editor:
        parser.description = "Create a self-contained offline editor from a saved bundle."
    if editing:
        parser.description = "Apply a versioned edit specification to an offline saved bundle."
        parser.add_argument("edits", help="image-score-edit version 1 JSON file")
    parser.add_argument("output", help="fresh output directory (parent must exist)")
    if not editing and not editor:
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
        if editor:
            from .editor import create_editor
            score = create_editor(image, output)
        else:
            score = edit_bundle(image, args["edits"], output) if editing else convert(image, output, **args)
    except KeyboardInterrupt:
        print("Cancelled; incomplete bundle removed.", file=sys.stderr)
        return 130
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        print(f"image-score: {error}", file=sys.stderr)
        return 2
    page = "editor.html" if editor else "report.html"
    print(f"Created {len(score['events'])} regions in {output}/{page}")
    return 0


def editor_main():
    return main(editor=True)


def edit_main():
    return main(editing=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["editor"]:
        del sys.argv[1]
        sys.exit(editor_main())
    if sys.argv[1:2] == ["edit"]:
        del sys.argv[1]
        sys.exit(edit_main())
    sys.exit(main())
