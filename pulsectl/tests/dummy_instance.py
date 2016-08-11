# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
import unittest, contextlib, atexit, signal
import os, sys, time, subprocess, tempfile, shutil, socket

if sys.version_info.major > 2: unicode = str

try: import pulsectl
except ImportError:
	sys.path.insert(1, os.path.join(__file__, *['..']*2))
	import pulsectl


def setup_teardown(cls):
	for sig in 'hup', 'term', 'int':
		signal.signal(getattr(signal, 'sig{}'.format(sig).upper()), lambda sig,frm: sys.exit())
	atexit.register(cls.tearDownClass)


class DummyTests(unittest.TestCase):

	tmp_dir = xdg_dir = proc = None
	sock_unix = sock_tcp4 = sock_tcp6 = None

	@classmethod
	def setUpClass(cls):
		setup_teardown(cls)

		if not cls.tmp_dir: cls.tmp_dir = tempfile.mkdtemp(prefix='pulsectl-tests.')
		tmp_path = ft.partial(os.path.join, cls.tmp_dir)

		cls.xdg_dir = tmp_path('runtime')
		if not os.path.exists(cls.xdg_dir): os.makedirs(cls.xdg_dir)

		# Pick some random available localhost ports
		bind = ( ['127.0.0.1', 0, socket.AF_INET],
			['::1', 0, socket.AF_INET6], ['127.0.0.1', 0, socket.AF_INET] )
		for spec in bind:
			addr, p, af = spec
			with contextlib.closing(socket.socket(af, socket.SOCK_STREAM)) as s:
				s.bind((addr, p))
				s.listen(1)
				spec[1] = s.getsockname()[1]
		cls.sock_unix = 'unix:{}'.format(os.path.join(cls.xdg_dir, 'pulse', 'native'))
		cls.sock_tcp4 = 'tcp4:{}:{}'.format(bind[0][0], bind[0][1])
		cls.sock_tcp6 = 'tcp6:[{}]:{}'.format(bind[1][0], bind[1][1])
		cls.sock_tcp_cli = tuple(bind[2][:2])

		if not cls.proc:
			cls.proc = subprocess.Popen(
				[ 'pulseaudio', '--daemonize=no', '--fail',
					'-nC', '--exit-idle-time=-1', '--log-level=error' ],
				env=dict(XDG_RUNTIME_DIR=cls.xdg_dir), stdin=subprocess.PIPE )
			for line in [
					'module-augment-properties',

					'module-default-device-restore',
					'module-rescue-streams',
					'module-always-sink',
					'module-intended-roles',
					'module-suspend-on-idle',
					'module-position-event-sounds',
					'module-role-cork',
					'module-filter-heuristics',
					'module-filter-apply',
					'module-switch-on-port-available',

					'module-native-protocol-tcp auth-anonymous=true'
						' listen={addr4} port={port4}'.format(addr4=bind[0][0], port4=bind[0][1]),
					'module-native-protocol-tcp auth-anonymous=true'
						' listen={addr6} port={port6}'.format(addr6=bind[1][0], port6=bind[1][1]),
					'module-native-protocol-unix',

					'module-null-sink',
					'module-null-sink' ]:
				if line.startswith('module-'): line = 'load-module {}'.format(line)
				cls.proc.stdin.write('{}\n'.format(line).encode('utf-8'))
				cls.proc.stdin.flush()
			timeout, checks, p = 4, 10, cls.sock_unix.split(':', 1)[-1]
			for n in range(checks):
				if not os.path.exists(p):
					time.sleep(float(timeout) / checks)
					continue
				break
			else: raise AssertionError(p)

	@classmethod
	def tearDownClass(cls):
		if cls.proc:
			cls.proc.stdin.close()
			timeout, checks = 4, 10
			for n in range(checks):
				if cls.proc.poll() is None:
					time.sleep(float(timeout) / checks)
					continue
				cls.proc.wait()
				break
			else: cls.proc.kill()
			cls.proc = None
		if cls.tmp_dir:
			shutil.rmtree(cls.tmp_dir)
			cls.tmp_dir = None


	def test_connect(self):
		with pulsectl.Pulse('t', server=self.sock_unix) as pulse: si = pulse.server_info()
		with pulsectl.Pulse('t', server=self.sock_tcp4) as pulse: si4 = pulse.server_info()
		self.assertEqual(vars(si), vars(si4))
		with pulsectl.Pulse('t', server=self.sock_tcp6) as pulse: si6 = pulse.server_info()
		self.assertEqual(vars(si), vars(si6))

	def test_server_info(self):
		with pulsectl.Pulse('t', server=self.sock_unix) as pulse:
			si, srcs, sinks = pulse.server_info(), pulse.source_list(), pulse.sink_list()
		self.assertEqual(len(srcs), 2)
		self.assertEqual(len(sinks), 2)

	def test_default_set(self):
		with pulsectl.Pulse('t', server=self.sock_unix) as pulse:
			(src1, src2), (sink1, sink2) = pulse.source_list()[:2], pulse.sink_list()[:2]
			self.assertNotEqual(sink1.name, sink2.name)
			self.assertNotEqual(src1.name, src2.name)

			pulse.default_set(sink1)
			pulse.default_set(sink1)
			pulse.default_set(src1)
			si = pulse.server_info()
			self.assertEqual(si.default_sink_name, sink1.name)
			self.assertEqual(si.default_source_name, src1.name)

			pulse.default_set(sink2)
			si = pulse.server_info()
			self.assertEqual(si.default_sink_name, sink2.name)
			self.assertEqual(si.default_source_name, src1.name)

			pulse.default_set(src2)
			pulse.default_set(src2)
			pulse.default_set(sink1)
			si = pulse.server_info()
			self.assertEqual(si.default_sink_name, sink1.name)
			self.assertEqual(si.default_source_name, src2.name)

			pulse.sink_default_set(sink2.name)
			pulse.source_default_set(src1.name)
			si = pulse.server_info()
			self.assertEqual(si.default_sink_name, sink2.name)
			self.assertEqual(si.default_source_name, src1.name)

			nx = 'xxx'
			self.assertNotIn(nx, [sink1.name, sink2.name])
			self.assertNotIn(nx, [src1.name, src2.name])
			with self.assertRaises(TypeError): pulse.sink_default_set(sink2.index)
			with self.assertRaises(pulsectl.PulseOperationFailed): pulse.sink_default_set(nx)
			with self.assertRaises(pulsectl.PulseOperationFailed): pulse.source_default_set(nx)
			si = pulse.server_info()
			self.assertEqual(si.default_sink_name, sink2.name)
			self.assertEqual(si.default_source_name, src1.name)

	def test_events(self):
		with pulsectl.Pulse('t', server=self.sock_unix) as pulse:
			sink = pulse.sink_list()[0]
			def ev_cb(ev):
				self.assertEqual(ev.facility, 'sink')
				self.assertEqual(ev.t, 'change')
				self.assertEqual(ev.index, sink.index)
				raise pulsectl.PulseLoopStop
			pulse.event_mask_set('all')
			pulse.event_callback_set(ev_cb)
			pulse.volume_set_all_chans(sink, 0.6)

	def test_cli(self):
		xdg_dir_prev = os.environ.get('XDG_RUNTIME_DIR')
		try:
			os.environ['XDG_RUNTIME_DIR'] = self.xdg_dir
			with contextlib.closing(pulsectl.connect_to_cli()) as s:
				s.write('dump\n')
				for line in s:
					if line == '### EOF\n': break
				else: raise AssertionError
				s.write(
					'load-module module-cli-protocol-tcp'
					' listen={} port={}\n'.format(*self.sock_tcp_cli) )
			with contextlib.closing(pulsectl.connect_to_cli(self.sock_tcp_cli)) as s:
				s.write('dump\n')
				for line in s:
					if line == '### EOF\n': break
				else: raise AssertionError
				s.write('unload-module module-cli-protocol-tcp\n')
		finally:
			if xdg_dir_prev is not None:
				os.environ['XDG_RUNTIME_DIR'] = xdg_dir_prev


if __name__ == '__main__': unittest.main()
