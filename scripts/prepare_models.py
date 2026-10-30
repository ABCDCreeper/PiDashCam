"""Install pinned HyperLPR models; never accept unverified local or remote files."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess

MODELS = {
 'litemodel_cls_96x_r1.onnx':'fe123688d3bf08b9ef029fcd57f3ac4644ac2e5ef8b9e7a676aacd5fad152142',
 'rpv3_mdict_160_r3.onnx':'35521a46fd8beaa6fd942f969bc666f4ef5c8433d2082eae002e6c94c5d307a2',
 'y5fu_320x_sim.onnx':'2a985dc63a5cc947ec36d18503d6fc0fd54525b9dba17f2fe29a71e86b44456d',
 'y5fu_640x_sim.onnx':'0306de937471b87f56eb3f5620815e7e7058f8ab7428e0734fc64627cc4d716c'}
COMMIT = 'ea1d0b15cc17364e3afda2b13ec83fee18042549'
parser = argparse.ArgumentParser()
parser.add_argument('--data', type=Path, required=True)
parser.add_argument('--source', type=Path)
args = parser.parse_args()
folder = args.data / '.hyperlpr3/20230229/onnx'
folder.mkdir(parents=True, exist_ok=True)
for name, digest in MODELS.items():
    target = folder / name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        continue
    temporary = folder / (name + '.partial')
    try:
        if args.source:
            shutil.copyfile(args.source / name, temporary)
        else:
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '10',
                            '--max-time', '180', '-o', str(temporary),
                            f'https://raw.githubusercontent.com/szad670401/HyperLPR/{COMMIT}/resource/models/onnx/{name}'], check=True)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Model SHA-256 mismatch: {name}')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
print('All four model SHA-256 checks passed')
