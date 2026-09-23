"""Read-only analysis of SSTV wav files to verify decoder timing constants."""
import numpy as np
from scipy.io import wavfile
from scipy.ndimage import median_filter

def load(fn):
    sr, d = wavfile.read(fn)
    if d.ndim > 1:
        d = d.mean(axis=1)
    x = d.astype(np.float64) / 32768.0
    return sr, x

def inst_freq(x, sr):
    x = x - np.mean(x)
    signs = np.sign(x); signs[signs==0]=1
    zc = np.where(np.diff(signs)!=0)[0]
    pos=[]
    for z in zc:
        if z+1 < len(x):
            y0,y1=x[z],x[z+1]
            pos.append(z + (-y0/(y1-y0)) if y1!=y0 else float(z))
    pos=np.array(pos)
    f=np.full(len(x),1500.0)
    if len(pos)>=2:
        periods=np.diff(pos)*2
        fr=sr/np.clip(periods,1,sr)
        for i in range(len(fr)):
            s=int(pos[i]); e=int(pos[i+1]) if i+1<len(pos) else len(x)
            f[s:e]=fr[i]
    f=median_filter(f,size=31)
    return f

def seg_summary(f, sr, t0, t1, label):
    i0=int(t0*sr); i1=int(t1*sr)
    seg=f[i0:i1]
    print(f"  [{label}] {t0:6.3f}-{t1:6.3f}s  mean={seg.mean():7.1f} med={np.median(seg):7.1f} "
          f"min={seg.min():7.1f} max={seg.max():7.1f} std={seg.std():6.1f}")

for fn in ['real_sstv.wav','syn_robot36.wav']:
    print('='*70)
    print(fn)
    sr,x=load(fn)
    # resample to 48000 like decoder
    from scipy.signal import resample
    n=int(len(x)*48000/sr)
    x48=resample(x,n)
    sr=48000
    f=inst_freq(x48,sr)
    print(f'duration={len(x48)/sr:.3f}s  (at {sr}Hz)')
    # header region 0..1.0s: print coarse frequency every 20ms
    print('--- header 0..1.2s (20ms buckets) ---')
    for t in np.arange(0,1.2,0.02):
        i0=int(t*sr); i1=int((t+0.02)*sr)
        m=np.median(f[i0:i1])
        bar='#'*int((m-900)/30)
        print(f'  {t:5.3f}s  f={m:7.1f} {bar}')
