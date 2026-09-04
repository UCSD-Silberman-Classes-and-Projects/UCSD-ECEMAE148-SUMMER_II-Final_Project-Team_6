exec(open('/tmp/sweep.py').read().split("print('rows_to_watch")[0])
print('hue-bound sweep, correct erode/dilate iterations, S80 V145, crop rtw.20 roff.70 cwd.64, W20-117\n')
for hl, hh in [(25,35),(24,36),(22,38),(20,40),(18,45),(18,50)]:
    sh_,bh_,lw,rw,n,k = test(0.20, 0.70, 0.64, hl=hl, hh=hh, sl=80, vl=145)
    print('  H %2d-%2d -> contours %2d kept %d  %s' % (hl,hh,n,len(k),[(x,w) for x,y,w in k]))
print()
print('and with the guide S/V (55/101):')
for hl, hh in [(25,35),(20,40),(18,50)]:
    sh_,bh_,lw,rw,n,k = test(0.20, 0.70, 0.64, hl=hl, hh=hh, sl=55, vl=101)
    print('  H %2d-%2d -> contours %2d kept %d  %s' % (hl,hh,n,len(k),[(x,w) for x,y,w in k]))
