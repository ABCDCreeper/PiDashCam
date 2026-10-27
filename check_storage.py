"""存储可靠性检查，不挂载设备或删除真实录像。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from PyQt5.QtCore import QObject
from core_camera import CameraWorker, SystemUtils

class StorageChecks(unittest.TestCase):
    def worker(self):
        worker = CameraWorker.__new__(CameraWorker)
        QObject.__init__(worker)
        worker.current_video_filename = ''
        return worker

    def test_usb_partition_inherits_parent_transport(self):
        tree = {'blockdevices': [{'name':'sda','tran':'usb','rm':False,'type':'disk',
                'children':[{'name':'sda1','pkname':'sda','rm':False,'type':'part','mountpoint':'/fixture'}]}]}
        usage = SimpleNamespace(total=10*1024**3,free=5*1024**3,percent=50)
        with patch('core_camera.sp.run',return_value=SimpleNamespace(stdout=json.dumps(tree))), patch('core_camera.os.path.exists',return_value=True), patch('core_camera.psutil.disk_usage',return_value=usage):
            devices=SystemUtils.get_storage_devices()
        self.assertEqual(len(devices),1)
        self.assertEqual(devices[0]['total_gb'],10)

    def test_cleanup_reclaims_multiple_segments_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('REC_1.mkv','REC_2.mkv','holiday.mp4','wifi_passwords.json'):
                (root/name).write_bytes(b'fixture')
            usage=[SimpleNamespace(total=100,free=2,percent=98),SimpleNamespace(total=100,free=8,percent=92),SimpleNamespace(total=100,free=30,percent=70)]
            with patch('core_camera.psutil.disk_usage',side_effect=usage):
                self.assertTrue(self.worker()._check_and_clean_disk(directory))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['holiday.mp4','wifi_passwords.json'])

    def test_no_recording_to_unmounted_directory(self):
        worker=self.worker()
        worker.storage_path='/missing/usb'
        worker._pending_storage_path=None
        worker._encoder_proc=None
        worker._record_start_monotonic=0
        worker._writer_lock=threading.RLock()
        worker.is_recording=True
        with patch('core_camera.os.path.ismount',return_value=False), patch('core_camera.os.makedirs') as mkdir:
            worker._handle_recording(None)
        mkdir.assert_not_called()
        self.assertFalse(worker.is_recording)

    def test_full_disk_without_app_recordings_fails_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'holiday.mp4'
            target.write_bytes(b'fixture')
            with patch('core_camera.psutil.disk_usage',return_value=SimpleNamespace(total=100,free=1,percent=99)):
                self.assertFalse(self.worker()._check_and_clean_disk(directory))
            self.assertTrue(target.exists())

if __name__=='__main__': unittest.main(verbosity=2)
