"""Run QEMU under TCG, save serial evidence, stop after the probe result or timeout."""
import sys,subprocess,time,selectors,json,os
from pathlib import Path
work,results=map(Path,sys.argv[1:3]);partuuid,mode=sys.argv[3:5]
results.mkdir(exist_ok=True)
base=['docker','run','--rm','--name','pidashcam-vm-test','--network','none','-v',f'{work}:/vm','pidashcam-qemu','qemu-system-aarch64']
common=['-accel','tcg,thread=multi','-smp','4','-m','2G','-display','none','-serial','stdio','-monitor','none','-no-reboot','-nic','none']
cmdline=f'root=PARTUUID={partuuid} rootfstype=ext4 rootwait rw console=ttyAMA0,115200 loglevel=4 systemd.show_status=yes'
if mode=='raspi4b':
    args=['-M','raspi4b','-kernel','/vm/kernel8.img','-dtb','/vm/rpi4.dtb',
          '-drive','file=/vm/test.img,if=sd,format=raw','-append',cmdline+' earlycon=pl011,mmio32,0xfe201000']
    if (work/'rpi-initrd').exists():args+=['-initrd','/vm/rpi-initrd']
    timeout=300
else:
    args=['-M','virt','-cpu','max','-kernel','/vm/virt-kernel','-initrd','/vm/virt-initrd',
          '-drive','file=/vm/test.img,if=none,id=rootdisk,format=raw','-device','virtio-blk-pci,drive=rootdisk',
          '-object','rng-random,filename=/dev/urandom,id=rng0','-device','virtio-rng-pci,rng=rng0',
          '-append',cmdline]
    timeout=900
process=subprocess.Popen(base+common+args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
os.set_blocking(process.stdout.fileno(),False)
selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
start=time.monotonic();buffer=b'';finished=False;report=None
with (results/f'{mode}-serial.log').open('wb') as log:
    while time.monotonic()-start<timeout:
        for key,_ in selector.select(timeout=1):
            chunk=os.read(key.fileobj.fileno(),65536)
            if not chunk:continue
            log.write(chunk);log.flush();buffer+=chunk
            while b'\n' in buffer:
                raw,buffer=buffer.split(b'\n',1)
                line=raw.decode(errors='replace')
                if 'PIDASHCAM_VM_RESULT ' in line:
                    payload=line.split('PIDASHCAM_VM_RESULT ',1)[1]
                    try:
                        report=json.loads(payload);report['emulator']=mode
                        report['interactive_enrollment_tested']=False
                        report['pi_hardware_tested']=False
                    except json.JSONDecodeError:pass
                if 'PIDASHCAM_VM_PROBE_END' in line:finished=True
            if finished:break
        if finished or process.poll() is not None:break
    if report:
        (results/f'{mode}-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    else:
        print(f'{mode}: no guest probe result after {time.monotonic()-start:.1f}s; see serial log',flush=True)
subprocess.run(['docker','stop','-t','5','pidashcam-vm-test'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
try:process.wait(timeout=10)
except subprocess.TimeoutExpired:process.kill();process.wait()
