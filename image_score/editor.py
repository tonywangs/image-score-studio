"""Generate a self-contained editor from a strictly validated saved bundle."""
import base64
import io
from pathlib import Path

from . import core, edit


def create_editor(bundle, destination):
    destination = Path(destination).absolute()
    edit.require(not destination.resolve().is_relative_to(Path(bundle).resolve()),
                 'output cannot be inside input bundle')
    score, image, parent = edit.load_bundle(bundle)
    png = io.BytesIO()
    image.save(png, format='PNG')
    payload = core.canonical({'score': score, 'parent': parent,
                              'image': base64.b64encode(png.getvalue()).decode('ascii')})
    template = Path(__file__).with_name('editor.html').read_bytes()
    script = Path(__file__).with_name('editor.js').read_bytes()
    size = len(template) - len(b'__PAYLOAD__') - len(b'__SCRIPT__') + len(script) + 4*((len(payload)+2)//3)
    edit.require(size <= core.MAX_FILE, 'editor exceeds 10 MiB output limit')
    html = template.replace(b'__PAYLOAD__', base64.b64encode(payload)).replace(b'__SCRIPT__', script)
    edit.require(len(html) == size, 'editor size mismatch')
    core.publish({'editor.html': html}, destination)
    return score
