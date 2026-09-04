import cv2, numpy as np

frame = cv2.imread('/tmp/track.jpg')          # BGR, same as imgmsg_to_cv2 gives
height, width = frame.shape[:2]

# --- exact crop math from calibration_node.py ---
crop_width_decimal, rows_to_watch_decimal, rows_offset_decimal = 0.64, 0.2, 0.5
rows_to_watch = int(height * rows_to_watch_decimal)
rows_offset   = int(height * (1 - rows_offset_decimal))
start_height  = int(height - rows_offset)
bottom_height = int(start_height + rows_to_watch)
left_width    = int((width / 2) * (1 - crop_width_decimal))
right_width   = int((width / 2) * (1 + crop_width_decimal))
img = frame[start_height:bottom_height, left_width:right_width]
print('frame %dx%d -> ROI rows %d:%d cols %d:%d = %dx%d'
      % (width, height, start_height, bottom_height, left_width, right_width,
         img.shape[1], img.shape[0]))

hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
total = img.shape[0] * img.shape[1]


def run(name, hl, hh, sl, sh, vl, vh, gray_lower=61, kernal=3, ero=1, dil=4):
    lower, higher = np.array([hl, sl, vl]), np.array([hh, sh, vh])
    mask = cv2.inRange(hsv, lower, higher)
    bitwise = cv2.bitwise_and(hsv, hsv, mask=mask)
    gray = cv2.cvtColor(bitwise, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, gray_lower, 255, cv2.THRESH_BINARY)
    k = np.ones((kernal, kernal), np.uint8)
    blurred = cv2.blur(bw, (kernal, kernal))
    bw2 = cv2.dilate(cv2.erode(blurred, k, iterations=ero), k, iterations=dil)
    _, bw2 = cv2.threshold(bw2, gray_lower, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(bw2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    widths = sorted((cv2.boundingRect(c)[2] for c in cnts), reverse=True)
    print('%-22s mask %5.2f%%  final %5.2f%%  contours %3d  widths %s'
          % (name, 100.0 * np.count_nonzero(mask) / total,
             100.0 * np.count_nonzero(bw2) / total, len(cnts), widths[:8]))
    return mask, bw2


print()
cur_mask, cur_bw = run('CURRENT yaml', 18, 50, 80, 255, 145, 255)
run('tighter yellow', 20, 35, 110, 255, 150, 255)
run('narrow yellow', 22, 32, 130, 255, 160, 255)
run('white lines only', 0, 179, 0, 60, 180, 255)

# --- what HSV do the yellow pixels actually have? ---
ref = cv2.inRange(hsv, np.array([15, 90, 130]), np.array([40, 255, 255]))
ys, xs = np.nonzero(ref)
if len(ys):
    h, s, v = hsv[ys, xs, 0], hsv[ys, xs, 1], hsv[ys, xs, 2]
    print('\nyellow-ish pixels in ROI: %d (%.2f%%)' % (len(ys), 100.0 * len(ys) / total))
    for lbl, ch in (('H', h), ('S', s), ('V', v)):
        p = np.percentile(ch, [1, 5, 50, 95, 99])
        print('  %s  p1=%3d p5=%3d med=%3d p95=%3d p99=%3d' % (lbl, *p.astype(int)))

cv2.imwrite('/tmp/roi.png', img)
cv2.imwrite('/tmp/mask_cur.png', cur_mask)
cv2.imwrite('/tmp/bw_cur.png', cur_bw)
