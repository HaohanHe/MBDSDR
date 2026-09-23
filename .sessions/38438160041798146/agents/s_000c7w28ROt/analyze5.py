"""Average frequency profile over all groups to reveal fixed segment boundaries."""
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample
from scipy.ndimage import median_filter
import sys; sys.path.insert(0,'.')
import mbdsdr_ai.sstv_decoder as S

sr,d=wavfile.read('real_sstv.wav')
if d.ndim>1: d=d.mean(axis=1)
x=d.astype(np.float32)/32768.0
x=resample(x,int(len(x)*48000/sr)).astype(np.float32); sr=48000
freq=S._instantaneous_frequency(x,sr)
vis,data_start=S._detect_vis_header(freq,sr)
markers,pulse_ms,period_ms=S._find_sync_markers(freq,sr,data_start,sync_ms_nom=9.0)
fr=np.where(np.isfinite(freq),freq,1500.0)

# average over first 100 groups
W=int(period_ms*sr/1000.0)
acc=np.zeros(W); cnt=0
for s in markers[:100]:
    if s+W>=len(fr): break
    acc+=fr[s:s+W]; cnt+=1
avg=acc/cnt
print(f'averaged over {cnt} groups, period={period_ms:.1f}ms')
print('time(ms)  avg_freq  std_across_groups')
for t in np.arange(0,period_ms,4.0):
    i0=int(t*sr/1000); i1=int((t+4)*sr/1000)
    # collect this bin across all groups
    bins=[]
    for s in markers[:100]:
        if s+W>=len(fr): continue
        bins.append(fr[s+i0:s+i1])
    bins=np.array(bins)
    m=avg[i0:i1].mean(); sd=bins.mean(axis=1).std()
    print(f'  +{t:6.1f}  f={m:7.1f}  across-group-std={sd:6.1f}')
