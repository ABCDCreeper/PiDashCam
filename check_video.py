import cv2, os, glob
files = glob.glob('/mnt/usb_*/REC_*.mkv')
if files:
    files.sort(key=os.path.getmtime)
    for f in files[-3:]:
        sz = os.path.getsize(f)
        c = cv2.VideoCapture(f)
        fps = c.get(cv2.CAP_PROP_FPS)
        fc = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f'{os.path.basename(f)}: {sz/1024/1024:.1f}MB {w}x{h} {fps:.0f}fps {fc}frames')
        c.release()
else:
    print('No files')
