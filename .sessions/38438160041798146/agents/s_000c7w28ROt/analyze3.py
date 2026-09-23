"""Measure grouped Robot36 segment boundaries in real_sstv.wav."""
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
per=np.diff(pos)*2
fr=sr/np.clip(per,1,sr)
for i in range(len(fr)):
    a=int(pos[i]); b=int(pos[i+1]) if i+1<len(pos) else len(x)
    f[a:b]=fr[i]
f=median_filter(f,size=31)

# sync pulses at 1.215, 1.515 ... examine window 1.215-1.515 in 2ms buckets
print("One grouped period 1.215->1.515s (2ms buckets):")
t0=1.215
for t in np.arange(0,0.300,0.002):
    i0=int((t0+t)*sr); i1=int((t0+t+0.002)*sr)
    seg=f[i0:i1]
    m=np.median(seg); sd=seg.std()
    tag=''
    if abs(m-1200)<80: tag='SYNC'
    elif abs(m-1500)<80 and sd<50: tag='PORCH'
    elif sd>120: tag='LUMA?'
    elif sd<80: tag='CHROMA?'
    print(f'  +{t*1000:6.1f}ms  f={m:7.1f} std={sd:6.1f} {tag}')
