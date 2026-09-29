"""Build a wheel and install into an isolated target, with pip networking disabled."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image


def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);wheels=tmp/'wheels';wheels.mkdir()
        subprocess.run([sys.executable,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation','--wheel-dir',str(wheels),str(root)],check=True)
        target=tmp/'installed'
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--target',str(target),str(next(wheels.glob('*.whl')))],check=True)
        # Pillow comes from the already-provisioned environment, copied into isolation.
        import PIL
        import shutil
        site=Path(PIL.__file__).parent.parent
        shutil.copytree(site/'PIL',target/'PIL')
        for directory in site.glob('pillow.libs'):shutil.copytree(directory,target/directory.name)
        Image.new('RGB',(8,4),(240,120,60)).save(tmp/'fixture.png')
        executable=target/'bin'/'image-score'
        env={**os.environ,'PYTHONPATH':str(target),'PIP_NO_INDEX':'1'}
        # -S avoids site packages and the editable checkout. Execute installed entry script.
        subprocess.run([sys.executable,'-S',str(executable),str(tmp/'fixture.png'),str(tmp/'bundle')],cwd=tmp,env=env,check=True)
        assert (tmp/'bundle'/'report.html').stat().st_size>1000
        assert len(list((tmp/'bundle').iterdir()))==6
    print('Isolated installed entry point: PASS (offline wheel build/install and execution)')


if __name__=='__main__':main()
