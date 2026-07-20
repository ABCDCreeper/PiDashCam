import cv2, os, glob
files = glob.glob('/mnt/usb_*/REC_*.mkv')
if files:
    f = sorted(files)[-1]
    c = cv2.VideoCapture(f)
    fps = c.get(cv2.CAP_PROP_FPS)
    frames = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = frames / fps if fps > 0 else 0
    size = os.path.getsize(f)
    w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f'File: {os.path.basename(f)}')
    print(f'Resolution: {w}x{h}, FPS: {fps:.1f}, Frames: {frames}, Duration: {dur:.1f}s, Size: {size/1024:.0f}KB')
    c.release()
else:
    print('No files found')
