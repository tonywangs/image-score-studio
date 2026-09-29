"""Small synthetic fixtures, generated without network or private data."""
import argparse
from pathlib import Path
from PIL import Image


def generate(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    for name,size in [('gradient',(127,83)),('large',(2000,2000))]:
        im=Image.new('RGB',size)
        # Repeated colored columns give a compressible but nonuniform large input.
        row=Image.new('RGB',(size[0],1));row.putdata([(x*255//max(1,size[0]-1),64,(size[0]-1-x)*255//max(1,size[0]-1)) for x in range(size[0])])
        for y in range(size[1]):im.paste(row,(0,y))
        im.save(destination/(name+'.png'))
    Image.new('RGBA',(13,9),(220,130,20,128)).save(destination/'alpha.png')
    Image.new('RGB',(16,16),(0,0,0)).save(destination/'black.png')
    Image.new('RGB',(35,19),(160,40,200)).save(destination/'photo.jpg',quality=90)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination');generate(p.parse_args().destination)
