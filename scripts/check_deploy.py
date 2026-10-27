"""Exercise installer transitions with fake host commands in a temporary filesystem."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'deploy.sh'

class DeploymentChecks(unittest.TestCase):
    def run_fixture(self, root, unhealthy=False):
        host = root / 'host'
        host.mkdir(exist_ok=True)
        (host / 'os-release').write_text('ID=debian\nVERSION_ID=12\nPRETTY_NAME=Fixture\n')
        (host / 'systemd/system').mkdir(parents=True, exist_ok=True)
        binary = root / 'bin'
        binary.mkdir(exist_ok=True)
        fake = '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
if name=='dpkg': print('arm64')
elif name=='git':
 if 'clone' in args: Path(args[-1]).mkdir(parents=True)
 else: print('a'*40)
elif name=='python3':
 if args[:2]==['-m','venv']:
  target=Path(args[-1])/'bin';target.mkdir(parents=True)
  p=target/'python';p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
 else: os.execv(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON']]+args)
elif name=='systemctl':
 if 'display-manager' in args: sys.exit(3)
elif name=='curl':
 if os.environ.get('UNHEALTHY')=='1': sys.exit(22)
 print('{"deployment_revision":"'+'a'*40+'"}')
'''
        # env's python3 resolves this shim again: pin the interpreter in each shebang.
        fake = fake.replace('#!/usr/bin/env python3', '#!' + sys.executable)
        for name in ('apt-get','git','dpkg','python3','systemctl','systemd-analyze','curl','sleep','journalctl'):
            path=binary/name
            path.write_text(fake)
            path.chmod(0o755)
        script=SOURCE.read_text()
        for original,replacement in [('/etc/os-release',str(host/'os-release')),
                                     ('/run/systemd/system',str(host/'systemd/system')),
                                     ('/etc/systemd/system',str(host/'systemd/system')),
                                     ('/run/pidashcam-deploy.lock',str(root/'lock')),
                                     ('/opt/pidashcam',str(root/'opt')),
                                     ('/var/lib/pidashcam',str(root/'data'))]:
            script=script.replace(original,replacement)
        script=script.replace('[[ $EUID -eq 0 ]]','[[ 1 -eq 1 ]]')
        target=root/'deploy.sh';target.write_text(script)
        return subprocess.run(['bash',str(target)],env={**os.environ,'PATH':str(binary)+':'+os.environ['PATH'],
                              'REAL_PYTHON':sys.executable,'UNHEALTHY':str(int(unhealthy))},
                              text=True,capture_output=True,timeout=20)

    def test_install_and_repeat_preserve_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=self.run_fixture(root)
            self.assertEqual(first.returncode,0,first.stderr)
            current=root/'opt/current'
            self.assertTrue((current/'venv/bin/python').exists())
            unit=(root/'host/systemd/system/pidashcam.service').read_text()
            self.assertIn('DASHCAM_REVISION='+'a'*40,unit)
            data=root/'data/Videos/fixture.json';data.write_text('preserve me')
            second=self.run_fixture(root)
            self.assertEqual(second.returncode,0,second.stderr)
            self.assertEqual(data.read_text(),'preserve me')

    def test_failed_update_restores_previous_release_and_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=self.run_fixture(root)
            self.assertEqual(first.returncode,0,first.stderr)
            previous=(root/'opt/current').resolve()
            unit=root/'host/systemd/system/pidashcam.service'
            original=unit.read_text()
            failed=self.run_fixture(root,unhealthy=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertEqual((root/'opt/current').resolve(),previous)
            self.assertEqual(unit.read_text(),original)
            self.assertIn('Restored previous application',failed.stderr)

    def test_invalid_cli_does_not_install(self):
        for args in (['--ref'],['--unknown']):
            result=subprocess.run(['bash',str(SOURCE),*args],capture_output=True)
            self.assertEqual(result.returncode,2)

if __name__=='__main__': unittest.main(verbosity=2)
