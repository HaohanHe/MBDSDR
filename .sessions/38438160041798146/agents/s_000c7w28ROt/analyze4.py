"""Precisely detect segment boundaries in grouped Robot36 by step detection."""
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample
from scipy.ndimage import median_filter

sr,d=wavfile.read('real_sstv.wav')
if d.ndim>1: d=d.mean(axis=1)
x=d.astype(np.float64)/32768.0
x=resample(x,int(len(x)*48000/sr)).astype(np.float32)
sr=48000
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
per=np.diff(pos)*2; fr=sr/np.clip(per,1,sr)
for i in range(len(fr)):
    a=int(pos[i]); b=int(pos[i+1]) if i+1<len(pos) else len(x)
    f[a:b]=fr[i]
f=median_filter(f,size=15)  # lighter median to preserve steps

# sync at 1.215. Examine 1.215..1.515, compute running median over 4ms, find steps
t0=1.215
print("4ms running medians, with step detector:")
prev=None
for t in np.arange(0,0.300,0.004):
    i0=int((t0+t)*sr); i1=int((t0+t+0.004)*sr)
    m=np.median(f[i0:i1])
    step = '' if prev is None else (f'  <<<STEP {m-prev:+.0f}' if abs(m-prev)>120 else '')
    print(f'  +{t*1000:6.1f}ms  f={m:7.1f}{step}')
    prev=m
