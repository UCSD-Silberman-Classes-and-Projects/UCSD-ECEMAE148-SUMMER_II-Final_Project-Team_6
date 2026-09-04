exec(open('/tmp/sweep.py').read().split("print('rows_to_watch")[0])
print('why only 1 detection here? sweep Width_min\n')
for wmin in (12, 16, 22, 30):
    sh_,bh_,l,rr,n,k = test(0.34, 0.46, 0.88, hl=20, hh=40, sl=40, vl=60, wmin=wmin, wmax=207)
    w=rr-l
    full = sorted([x+l for x,y,wd in k]); wd = sorted([d for x,y,d in k])
    bad = [f for f in full if f < 600]
    print('Wmin %2d -> contours %d kept %d widths %s  x=%s %s'
          % (wmin,n,len(k),wd,full,'JUNK' if bad else ''))
print()
print('equilibrium position vs centerline (0.5 = straight ahead):')
for cl in (0.70,0.75,0.78,0.80,0.85):
    off = cl - 0.5
    print('  centerline %.2f -> line held %.2f right of straight-ahead -> car sits that far LEFT of the line' % (cl,off))
