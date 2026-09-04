exec(open('/tmp/sweep.py').read().split("print('rows_to_watch")[0])
sh_,bh_,l,rr,n,k = test(0.34, 0.46, 0.88, hl=20, hh=40, sl=40, vl=60, wmin=22, wmax=207)
w = rr-l
print('ROI rows %d:%d cols %d:%d (width %d)' % (sh_,bh_,l,rr,w))
print('contours %d, kept %d' % (n, len(k)))
xs = sorted([x for x,y,wd in k])
print('detections full-frame x: %s' % [x+l for x in xs])
fr = [x/float(w) for x in xs]
print('fractions of ROI: %s' % [round(f,3) for f in fr])
print()
print('%-12s %-10s %-10s %s' % ('centerline','avg error','steer@Kp2','car sits...'))
for cl in (0.70, 0.75, 0.80, 0.85, 0.90):
    e = [(f-cl)/cl for f in fr]
    avg = sum(e)/len(e)
    side = 'LEFT of line (good)' if avg > 0.02 else ('ON the line' if abs(avg)<=0.02 else 'RIGHT of line (crossing!)')
    print('%-12.2f %+-10.3f %+-10.2f %s' % (cl, avg, 2.0*avg, side))
