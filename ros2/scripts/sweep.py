import cv2, numpy as np

frame = cv2.imread('/tmp/track.jpg')
height, width = frame.shape[:2]


def test(rtw, roff, cwd, hl=18, hh=50, sl=80, sh=255, vl=145, vh=255,
         wmin=20, wmax=117, gray_lower=61, kernal=3, ero=1, dil=4):
    rows_to_watch = int(height * rtw)
    rows_offset   = int(height * (1 - roff))
    sh_ = int(height - rows_offset)
    bh_ = int(sh_ + rows_to_watch)
    lw  = int((width / 2) * (1 - cwd))
    rw  = int((width / 2) * (1 + cwd))
    img = frame[sh_:bh_, lw:rw]
    if img.size == 0:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([hl, sl, vl]), np.array([hh, sh, vh]))
    bitwise = cv2.bitwise_and(hsv, hsv, mask=mask)
    gray = cv2.cvtColor(bitwise, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, gray_lower, 255, cv2.THRESH_BINARY)
    k = np.ones((kernal, kernal), np.uint8)
    bw = cv2.dilate(cv2.erode(cv2.blur(bw, (kernal, kernal)), k, iterations=ero),
                    k, iterations=dil)
    _, bw = cv2.threshold(bw, gray_lower, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    kept = []
    for c in cnts:
        (_, _), (w, h), _ = cv2.minAreaRect(c)
        if wmin < w < wmax:
            m = cv2.moments(c)
            if m['m00']:
                kept.append((int(m['m10'] / m['m00']), int(m['m01'] / m['m00']), int(w)))
    return sh_, bh_, lw, rw, len(cnts), kept


print('rows_to_watch / rows_offset sweep  (crop_width 0.64, H18-50 S80 V145, W20-117)\n')
for roff in (0.40, 0.50, 0.60, 0.62, 0.70):
    for rtw in (0.15, 0.20, 0.25, 0.30):
        r = test(rtw, roff, 0.64)
        if not r:
            continue
        sh_, bh_, lw, rw, n, kept = r
        print('rtw %.2f roff %.2f -> rows %3d:%3d  contours %2d  kept %d  %s'
              % (rtw, roff, sh_, bh_, n, len(kept), [(x, y, w) for x, y, w in kept]))
    print()
