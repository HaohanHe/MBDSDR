"""Measure exact VIS edges, sync periods, chroma range, and run the actual decoder."""
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample
from scipy.ndimage import median_filter
import sys
sys.path.insert(0, '/home/user/Doubao/chats/38438160041798146')

def load48(fn):
    sr,d=wavfile.read(fn)
    if d.ndim>1: d=d.mean(axis=1)
    x=d.astype(np.float64)/32768.0
    n=int(len(x)*48000/sr)
    return resample(x,n).astype(np.float32)

def inst_freq(x,sr=48000):
    x=x-np.mean(x)
    s=np.sign(x); s[s==0]=1
    zc=np.where(np.diff(s)!=0)[0]
    pos=[]
    for z in zc:
        if z+1<len(x):
            y0,y1=x[z],x[z+1]
            pos.append(z+(-y0/(y1-y0)) if y1!=y0 else float(z))
    pos=np.array(pos)
    f=np.full(len(x),1500.0)
    if len(pos)>=2:
        per=np.diff(pos)*2
        fr=sr/np.clip(per,1,sr)
        for i in range(len(fr)):
            a=int(pos[i]); b=int(pos[i+1]) if i+1<len(pos) else len(x)
            f[a:b]=fr[i]
    return median_filter(f,size=31)

def classify(f):
    if abs(f-1900)<120: return 'L'  # 1900 leader
    if abs(f-1200)<120: return 'S'  # 1200 sync/break
    if abs(f-1300)<80: return '1'   # VIS space?
    if abs(f-1100)<80: return '0'   # VIS mark?
    if abs(f-1500)<120: return 'P'
    return '.'

for fn in ['real_sstv.wav','syn_robot36.wav']:
    print('='*70); print(fn)
    x=load48(fn); f=inst_freq(x); sr=48000
    # exact run-length encoding of header 0..1.0s
    print('--- header RLE (1ms resolution) ---')
    runs=[]
    cur=classify(f[0]); start=0
    for i in range(1, int(1.0*sr)):
        c=classify(f[i])
        if c!=cur:
            runs.append((cur, start/sr, i/sr)); cur=c; start=i
    runs.append((cur,start/sr,1.0))
    for c,a,b in runs:
        if b-a>0.008:
            print(f'   {c}  {a:6.3f}-{b:6.3f}  ({(b-a)*1000:6.1f} ms)')
    # sync markers: 1200Hz pulses after 1s
    band=np.abs(f-1200)<=110
    band[:int(1.0*sr)]=False
    from scipy import ndimage as ndi
    lab=ndi.label(band)[0]
    starts=[]; widths=[]
    for k in range(1,lab.max()+1):
        idx=np.where(lab==k)[0]
        if len(idx)>=int(0.6*sr*4.5/1000):
            starts.append(idx[0]/sr); widths.append(len(idx)/sr*1000)
    starts=np.array(starts)
    gaps=np.diff(starts)*1000
    print(f'--- sync pulses: n={len(starts)}, median width={np.median(widths):.2f} ms ---')
    print(f'    first 10 start times(s): {np.round(starts[:10],3)}')
    print(f'    gaps(ms) first 15: {np.round(gaps[:15],1)}')
    vg=gaps[(gaps>100)&(gaps<1200)]
    print(f'    gap median={np.median(vg):.1f} ms, std={np.std(vg):.1f}, cv={np.std(vg)/np.median(vg):.3f}')
    # chroma range: look at whole signal, histogram of freq
    seg=f[int(1.0*sr):]
    print(f'--- whole-signal freq: p1={np.percentile(seg,1):.0f} p50={np.percentile(seg,50):.0f} p99={np.percentile(seg,99):.0f}')
    print(f'    min={seg.min():.0f} max={seg.max():.0f}')
