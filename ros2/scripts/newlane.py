exec(open('/tmp/sweep.py').read().split("print('rows_to_watch")[0])
sh_,bh_,l,rr,n,k = test(0.34, 0.46, 0.88, hl=20, hh=40, sl=40, vl=60, wmin=22, wmax=207)
w = rr-l
full = sorted([x+l for x,y,wd in k])
fr   = sorted([x/float(w) for x,y,wd in k])
print('ROI cols %d:%d (width %d)   contours %d, kept %d' % (l,rr,w,n,len(k)))
print('detections full-frame x : %s' % full)
print('fractions of ROI        : %s' % [round(f,3) for f in fr])
if fr:
    cl = sum(fr)/len(fr)
    print()
    print('centerline for ek=0 where the car sits now : %.2f' % cl)
    print('  -> line held %.2f LEFT of straight-ahead (0.5) = car sits right of it' % (0.5-cl))
    print()
    # normalisation: error divides by cam_center_line_x = width*cl
    print('normalisation check (this is the trap):')
    print('  old lane cl=0.80 -> divisor %d px' % int(w*0.80))
    print('  new lane cl=%.2f -> divisor %d px   (%.1fx more sensitive)'
          % (cl, int(w*cl), 0.80/cl))
    print('  so Kp 2.0 must become ~%.2f to give the same steering per unit drift' % (2.0*cl/0.80))
    print()
    print('%-10s %-12s %s' % ('centerline','avg error','steer @ that Kp'))
    for c in (round(cl-0.06,2), round(cl-0.03,2), round(cl,2), round(cl+0.03,2), round(cl+0.06,2)):
        if c <= 0.02: continue
        e = sum((f-c)/c for f in fr)/len(fr)
        print('%-10.2f %+-12.3f %+.2f' % (c, e, (2.0*cl/0.80)*e))
