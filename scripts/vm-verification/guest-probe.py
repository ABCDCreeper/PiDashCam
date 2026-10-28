"""Runs inside the virtual machine. Does not export generated passwords or keys."""
import os,sys,time,json,subprocess,tempfile,urllib.request,urllib.error,base64,threading
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['HOME']='/var/lib/pidashcam'
sys.path.insert(0,'/opt/pidashcam/current/app')
RESULT={'system_booted':True,'hardware_emulated':False,'camera':'synthetic 320x180',
        'gps':'not tested','display':'Qt offscreen, not HDMI','checks':{}}

def command(*args):
    return subprocess.run(args,capture_output=True,text=True,timeout=30)

def request(path,authenticated=True,method='GET',origin=None):
    headers={}
    if authenticated:headers['Authorization']='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
    if origin:headers['Origin']=origin
    req=urllib.request.Request('http://127.0.0.1'+path,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=20) as response:return response.status,response.read()
    except urllib.error.HTTPError as exc:return exc.code,exc.read()

print('PIDASHCAM_VM_PROBE_BEGIN',flush=True)
try:
    # Save the actual kiosk result before starting a separate offscreen application.
    RESULT['kiosk_service']=command('systemctl','show','pidashcam','-p','ActiveState','-p','SubState').stdout.strip()
    RESULT['kiosk_journal']=command('journalctl','-u','pidashcam','-b','-n','25','--no-pager').stdout
    command('systemctl','stop','pidashcam')
    RESULT['checks']['nginx_active']=command('systemctl','is-active','--quiet','nginx').returncode==0
    pwfile=Path('/var/lib/pidashcam/admin-password')
    password=pwfile.read_text().split('Password: ',1)[1].strip()
    RESULT['checks']['unique_password_generated']=len(password)>=30
    RESULT['checks']['unauthenticated_denied']=request('/api/status',False)[0]==401
    RESULT['checks']['cross_origin_denied']=request('/api/status',True,origin='https://example.invalid')[0]==403
    hostkeys=list(Path('/etc/ssh').glob('ssh_host_*_key'))
    RESULT['checks']['ssh_keys_generated']=bool(hostkeys)
    import cv2,numpy as np,onnxruntime as ort
    cv2.setNumThreads(1)
    original_session=ort.InferenceSession
    def session(*args,**kwargs):
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        args=list(args)
        if len(args)>1:args[1]=options
        else:kwargs['sess_options']=options
        return original_session(*args,**kwargs)
    ort.InferenceSession=session
    from PyQt5.QtWidgets import QApplication,QListWidgetItem
    from PyQt5.QtCore import Qt
    import core_camera as core
    class Camera:
        def __init__(self):self.width,self.height,self.fps,self.n,self.open=320,180,5,0,True
        def set(self,p,v):
            if p==cv2.CAP_PROP_FRAME_WIDTH:self.width=int(v)
            elif p==cv2.CAP_PROP_FRAME_HEIGHT:self.height=int(v)
            elif p==cv2.CAP_PROP_FPS:self.fps=float(v)
            return True
        def get(self,p):return {cv2.CAP_PROP_FRAME_WIDTH:self.width,cv2.CAP_PROP_FRAME_HEIGHT:self.height,cv2.CAP_PROP_FPS:self.fps}.get(p,0)
        def isOpened(self):return self.open
        def release(self):self.open=False
        def read(self):
            self.n+=1
            frame=np.zeros((self.height,self.width,3),np.uint8)
            cv2.rectangle(frame,(self.n%100,30),(self.n%100+60,100),(70,150,210),-1)
            return self.open,frame
    actual_capture=cv2.VideoCapture
    cv2.VideoCapture=lambda source=0,*a,**kw:Camera() if isinstance(source,int) else actual_capture(source,*a,**kw)
    STORAGE='/mnt/vm-usb'
    Path(STORAGE).mkdir(exist_ok=True)
    subprocess.run(['mount','-t','tmpfs','-o','size=128M','vm-test',STORAGE],check=True)
    core.SystemUtils.get_storage_devices=staticmethod(lambda:[dict(name='VM test storage',path=STORAGE,free_gb=.1,total_gb=.125,percent=1,is_usb=True)])
    core.SystemUtils._cleanup_stale_mounts=staticmethod(lambda:None)
    core.GPSWorker.start=lambda self:None
    class Worker(core.CameraWorker):
        def _load_storage_config(self):return ''
        def _save_storage_config(self,path):pass
        def _load_video_params(self):return dict(width=320,height=180,fps=5)
        def _detect_hw_encoder(self):return None
    core.CameraWorker=Worker
    import ui_mainwindow as ui
    from web_server import DashcamWebServer
    class Window(ui.DashcamMainWindow):
        def auto_start_recording(self):pass
    app=QApplication([]);window=Window();window.resize(800,480);window.show()
    worker=window.worker
    # Initial USB discovery starts recording; stop before the timed probe.
    worker.is_recording=False
    def wait(check,timeout=90):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            app.processEvents()
            if check():return True
            time.sleep(.05)
        return False
    if not wait(lambda:worker._frame_count>=5 and worker.lpr_catcher is not None):
        raise RuntimeError('Camera/model readiness timed out in the emulator')
    RESULT['checks']['model_loaded']=worker.lpr_catcher is not None
    RESULT['checks']['preview_frames']=worker._frame_count>=5
    RESULT['checks']['mount_validated']=worker._storage_ready(STORAGE)
    api=DashcamWebServer(worker,host='127.0.0.1',port=8080);api.start()
    code,body=request('/api/status');RESULT['checks']['authenticated_api']=code==200 and json.loads(body)['camera']['lpr_ready']
    code,body=request('/api/snapshot');RESULT['checks']['snapshot_jpeg']=code==200 and body[:2]==b'\xff\xd8'
    start=time.monotonic();code,_=request('/api/record/start',method='POST')
    if code!=200:raise RuntimeError('Recording start rejected')
    wait(lambda:time.monotonic()-start>=7,timeout=35)
    request('/api/record/stop',method='POST');elapsed=time.monotonic()-start
    files=sorted(Path(STORAGE).glob('REC_*.mkv'),key=lambda p:p.stat().st_mtime)
    if not files:raise RuntimeError('No recording created')
    video=files[-1]
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_name,width,height:format=duration','-of','json',str(video)],timeout=30))
    duration=float(meta['format']['duration'])
    RESULT['recording']={'wall_seconds':round(elapsed,3),'video_seconds':duration,'metadata':meta}
    RESULT['checks']['h264_recording']=meta['streams'][0]['codec_name']=='h264' and duration>1
    RESULT['checks']['duration_matches']=abs(duration-elapsed)<2
    progress=[]
    worker.playback_progress.connect(lambda current,total,_:progress.append(current))
    window.play_selected_video(QListWidgetItem(video.name))
    RESULT['checks']['playback']=wait(lambda:bool(progress),timeout=45)
    worker.is_paused=True
    wait(lambda:False,timeout=.5)
    previous=progress[-1] if progress else 0
    window.on_slider_moved(min(10,max(0,int(duration*5)-2)))
    RESULT['checks']['paused_seek']=wait(lambda:bool(progress) and progress[-1]!=previous,timeout=30)
    window.stop_playback_and_back()
    RESULT['checks']['ui_render']=window.grab().width()==800
    worker.stop();api.stop()
    RESULT['passed']=all(RESULT['checks'].values())
except Exception as exc:
    # No request headers, generated credentials or SSH key material are logged.
    RESULT['error_type']=type(exc).__name__
    RESULT['error']=str(exc)[:300]
    RESULT['passed']=False
print('PIDASHCAM_VM_RESULT '+json.dumps(RESULT,ensure_ascii=False),flush=True)
print('PIDASHCAM_VM_PROBE_END',flush=True)
subprocess.run(['systemctl','poweroff','--no-block'])
