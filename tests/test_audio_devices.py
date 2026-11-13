"""Read-only audio inventory and HTTP boundary; no microphones or sound playback."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from yiban.audio.devices import AudioDevices
import test_personal_http as fixture


class AudioDeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'tools').mkdir();(self.root/'tools/audio_devices.swift').write_text('// synthetic source')
        self.calls=[]
        self.body={'available':True,'devices':[{'name':'虚构内置输出','input_channels':0,'output_channels':2,'default_output':True,'muted':False,'volume_percent':2.0,'sample_rate':48000,'uid':'never-return-device-uid','machine_model':'never-return-machine-model'}]}
        def runner(args,**kw):
            self.calls.append(args)
            if args[0]=='/usr/bin/swiftc':Path(args[-1]).write_bytes(b'fake executable');return subprocess.CompletedProcess(args,0,stdout=b'',stderr=b'')
            return subprocess.CompletedProcess(args,0,stdout=json.dumps(self.body).encode(),stderr=b'')
        self.service=AudioDevices(self.root,runner=runner,system=lambda:'Darwin')

    def test_metadata_allowlist_compile_cache_and_no_side_effect_commands(self):
        result=self.service.describe();self.assertTrue(result['available']);self.assertEqual(result['devices'][0]['volume_percent'],2)
        raw=json.dumps(result);self.assertNotIn('never-return',raw);self.assertNotIn('uid',raw)
        self.assertEqual(len(self.calls),2)
        result['devices'].clear()
        self.assertEqual(len(self.service.describe()['devices']),1)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(self.calls[0][0].endswith('swiftc'))
        self.assertTrue(Path(self.calls[1][0]).name.startswith('audio-devices-'))
        self.assertFalse(any(x in str(self.calls) for x in ('osascript','say','afplay','AudioObjectSetPropertyData')))

    def test_unsupported_platform_does_not_run_any_native_command(self):
        service=AudioDevices(self.root,runner=lambda *a,**k:self.fail('must not run'),system=lambda:'Windows')
        result=service.describe();self.assertFalse(result['available']);self.assertEqual(result['code'],'unsupported_platform')

    def test_timeout_and_invalid_metadata_are_explained_without_raw_errors(self):
        def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('sensitive device details',1)
        service=AudioDevices(self.root,runner=timeout,system=lambda:'Darwin')
        result=service.describe();self.assertEqual(result['code'],'system_timeout');self.assertNotIn('sensitive',json.dumps(result))
        self.body={'available':True,'devices':[{'name':'invalid','input_channels':-1,'output_channels':0}]}
        self.assertEqual(self.service.describe()['code'],'system_unavailable')

    def test_missing_volume_and_mute_remain_unknown_not_healthy(self):
        self.body['devices'][0].pop('muted');self.body['devices'][0]['volume_percent']=float('nan')
        row=self.service.describe()['devices'][0]
        self.assertNotIn('muted',row);self.assertNotIn('volume_percent',row)

    def test_inventory_size_is_bounded(self):
        row=self.body['devices'][0];self.body['devices']=[row]*200
        self.assertLessEqual(len(self.service.describe()['devices']),80)

    def test_swift_source_is_read_only_and_has_no_identifiers(self):
        source=(Path(__file__).resolve().parents[1]/'tools/audio_devices.swift').read_text()
        self.assertIn('AudioObjectGetPropertyData',source)
        for forbidden in ('AudioObjectSetPropertyData','kAudioDevicePropertyDeviceUID','AVCaptureSession','AVAudioRecorder'):
            self.assertNotIn(forbidden,source)


class AudioDeviceHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def test_metadata_route_is_local_only_and_does_not_call_speech(self):
        origin=f'http://127.0.0.1:{self.http.server_port}'
        fake={'available':True,'devices':[],'code':'ok','message':'synthetic read-only state'}
        with patch.object(self.app.AUDIO_DEVICES,'describe',return_value=fake) as describe,patch.object(self.app.SERVICES.tts,'synthesize',side_effect=AssertionError('must not synthesize')),patch.object(self.app.SERVICES.asr,'transcribe',side_effect=AssertionError('must not capture')):
            with urllib.request.urlopen(origin+'/api/audio/devices') as response:
                result=json.load(response)
                self.assertEqual({k:v for k,v in result.items() if k!="services"},fake)
                self.assertEqual(result["services"]["tts"],False)
            request=urllib.request.Request(origin+'/api/audio/devices',headers={'Origin':'https://other.invalid'})
            with self.assertRaises(urllib.error.HTTPError) as result:urllib.request.urlopen(request)
            self.assertEqual(result.exception.code,403);self.assertEqual(describe.call_count,1)
        with urllib.request.urlopen(origin+'/audio-devices.js') as response:
            self.assertIn(b'capturePlan',response.read())
