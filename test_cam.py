import cv2
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FOURCC, 1196444237)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
print(f'Requested: 1920x1080, Got: {int(w)}x{int(h)}')
ret, frame = cap.read()
if ret:
    print(f'Frame shape: {frame.shape}')
else:
    print('Frame read failed')
cap.release()
