"""Read-only system audio metadata; browser route preferences remain in that browser."""
import copy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import threading
import time
from yiban.settings import ROOT


class AudioDevices:
    def __init__(self, root=ROOT, runner=subprocess.run, system=platform.system):
        self.root=Path(root);self.runner=runner;self.system=system
        self.lock=threading.Lock();self.cached=None;self.checked=0

    def describe(self):
        with self.lock:
            if self.cached is not None and time.monotonic()-self.checked<3:return copy.deepcopy(self.cached)
            result={'available':False,'devices':[],'code':'unsupported_platform',
                    'message':'当前平台未接入系统音频元数据，可使用下方浏览器设备列表；实际声音仍待测试。'}
            if self.system()=='Darwin':
                try:
                    source=self.root/'tools/audio_devices.swift'
                    digest=hashlib.sha256(source.read_bytes()).hexdigest()[:16]
                    binary=self.root/'.runtime'/('audio-devices-'+digest)
                    if not binary.exists():
                        binary.parent.mkdir(parents=True,exist_ok=True)
                        temporary=binary.with_suffix('.building')
                        try:
                            self.runner(['/usr/bin/swiftc',str(source),'-o',str(temporary)],capture_output=True,timeout=45,check=True)
                            temporary.replace(binary)
                        finally:temporary.unlink(missing_ok=True)
                    response=self.runner([str(binary)],capture_output=True,timeout=6,check=True)
                    if len(response.stdout)>100_000:raise ValueError('metadata too large')
                    doc=json.loads(response.stdout)
                    if not isinstance(doc,dict) or doc.get('available') is not True or not isinstance(doc.get('devices'),list):raise ValueError('invalid metadata')
                    devices=[]
                    for item in doc['devices'][:80]:
                        if not isinstance(item,dict) or not isinstance(item.get('name'),str):raise ValueError('invalid device')
                        row={'name':item['name'][:180]}
                        for key in ('input_channels','output_channels'):
                            value=item.get(key)
                            if type(value) is not int or not 0<=value<=1024:raise ValueError('invalid channels')
                            row[key]=value
                        for key in ('default_input','default_output','muted'):
                            if type(item.get(key)) is bool:row[key]=item[key]
                        for key,lo,hi in (('sample_rate',1,768000),('volume_percent',0,100)):
                            value=item.get(key)
                            if type(value) in (int,float) and lo<=value<=hi:row[key]=value
                        devices.append(row)
                    result={'available':True,'devices':devices,'code':'ok','message':'只读系统设备信息；不代表浏览器实际选择，不代表喇叭已经出声。'}
                except subprocess.TimeoutExpired:
                    result.update(code='system_timeout',message='系统音频信息读取超时，请稍后刷新；没有打开麦克风或播放声音。')
                except Exception:
                    result.update(code='system_unavailable',message='系统音频信息暂不可读；首次使用需安装 Xcode Command Line Tools。仍可查看浏览器设备与权限。')
            result['checked_at']=time.time()
            self.cached=result;self.checked=time.monotonic()
            return copy.deepcopy(result)
