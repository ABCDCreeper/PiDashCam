"""Static checks for image-build boundaries; does not mount images or install packages."""
from pathlib import Path
import ast
import re
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[2]

class ImageConfigChecks(unittest.TestCase):
    def test_shell_syntax_and_embedded_python(self):
        for name in ('build.sh','provision.sh','firstboot.sh'):
            path=ROOT/'scripts/image'/name
            subprocess.run(['bash','-n',str(path)],check=True)
            for code in re.findall(r"<<'PY'\n(.*?)\nPY",path.read_text(),re.S):
                ast.parse(code,filename=name)

    def test_native_arm64_read_only_workflow(self):
        text=(ROOT/'.github/workflows/build-image.yml').read_text()
        self.assertIn('runs-on: ubuntu-24.04-arm',text)
        self.assertIn('contents: read',text)
        self.assertIn('persist-credentials: false',text)
        self.assertIn('workflow_dispatch:',text)
        self.assertNotIn('pull_request_target',text)
        self.assertNotIn('secrets.',text)

    def test_service_template_exists_and_build_verifies_base(self):
        deploy=(ROOT/'deploy.sh').read_text()
        unit=deploy.split("<<'UNIT'\n",1)[1].split('\nUNIT\n',1)[0]
        self.assertIn('User=root',unit)
        self.assertIn('RuntimeDirectoryMode=0700',unit)
        build=(ROOT/'scripts/image/build.sh').read_text()
        self.assertRegex(build,r'BASE_SHA=[a-f0-9]{64}')
        self.assertIn('sha256sum --check --strict',build)
        self.assertIn('e2fsck -fn',build)
        self.assertIn('preserving $WORK for recovery',build)

    def test_credentials_are_not_created_by_image_builder(self):
        first=(ROOT/'scripts/image/firstboot.sh').read_text()
        provision=(ROOT/'scripts/image/provision.sh').read_text()
        self.assertIn('openssl rand -base64 24',first)
        self.assertIn('umask 077',first)
        self.assertNotIn('apt-get',first)
        self.assertNotRegex(provision,r'(?m)^/usr/local/sbin/pidashcam-firstboot\s*$')
        self.assertIn('DASHCAM_HTTP_HOST=127.0.0.1',provision)
        self.assertIn('auth_basic_user_file',provision)
        self.assertIn('proxy_hide_header Access-Control-Allow-Origin',provision)
        self.assertIn('passwd -l root',provision)

    def test_all_model_downloads_have_hashes(self):
        text=(ROOT/'scripts/image/provision.sh').read_text()
        names=re.findall(r"'([^']+\.onnx)':'([a-f0-9]{64})'",text)
        self.assertEqual(len(names),4)
        self.assertIn('hashlib.sha256',text)
        self.assertIn('model(np.zeros',text)
        self.assertIn('libx264',text)

if __name__=='__main__': unittest.main(verbosity=2)
