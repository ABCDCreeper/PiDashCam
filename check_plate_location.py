"""Plate log provenance: fresh fixes only, no synthetic production coordinates."""
import json,tempfile,threading,time,unittest
from pathlib import Path
from PyQt5.QtCore import QObject
from core_camera import CameraWorker

class PlateLocationChecks(unittest.TestCase):
    def record(self, fix, age):
        worker=CameraWorker.__new__(CameraWorker);QObject.__init__(worker)
        worker.last_log_time=0;worker.is_recording=False;worker.current_video_filename='fixture.mkv'
        worker._plates_lock=threading.Lock()
        worker.gps_data=dict(has_fix=fix,serial_error=None,updated_monotonic=time.monotonic()-age,
                             latitude=31.0,longitude=121.0)
        with tempfile.TemporaryDirectory() as folder:
            worker.storage_path=folder;worker._log_plate_to_json('fixture',.99)
            return json.loads((Path(folder)/'plates_log.json').read_text())[0]

    def test_fresh_fix_records_source_and_coordinates(self):
        row=self.record(True,0)
        self.assertEqual(row['time_source'],'system_clock')
        self.assertEqual(row['location_source'],'gps_nmea')
        self.assertEqual((row['latitude'],row['longitude']),(31.0,121.0))

    def test_no_fix_has_null_location(self):
        row=self.record(False,0)
        self.assertFalse(row['gps_has_fix']);self.assertIsNone(row['location_source'])
        self.assertIsNone(row['latitude']);self.assertIsNone(row['longitude'])

    def test_stale_fix_has_null_location(self):
        row=self.record(True,10)
        self.assertFalse(row['gps_has_fix']);self.assertIsNone(row['latitude'])

if __name__=='__main__':unittest.main(verbosity=2)
