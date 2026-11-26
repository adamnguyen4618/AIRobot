#!/usr/bin/python 
# -*- coding: utf-8 -*-
import socket
import struct
import threading
import time
from Command import COMMAND as cmd

CMD_PORT = 4000
VIDEO_PORT = 7000
CONNECT_TIMEOUT = 3.0
# Blocking reads wake up this often so stop/reconnect requests are noticed quickly.
POLL_TIMEOUT = 0.5
# With video on, the car streams continuously; this much silence means the link is dead.
STALE_TIMEOUT = 6.0
# The car firmware calls ESP.restart() whenever a client disconnects, so wait for it to boot.
RECONNECT_DELAY = 2.0
POWER_INTERVAL = 3.0
# The car answers every CMD_POWER poll, so a silent command link is dead.
CMD_STALE_TIMEOUT = 10.0
MAX_FRAME_BYTES = 512 * 1024


def is_valid_jpeg(buf):
    return buf[:2] == b'\xff\xd8' and buf.rstrip(b'\0\r\n').endswith(b'\xff\xd9')


def _close_socket(sock):
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class VideoStreaming:
    """Command (port 4000) + video (port 7000) client with automatic reconnect.

    Callbacks are invoked from worker threads; the GUI must marshal them to the UI thread.
    """

    def __init__(self):
        self.on_frame = None    # callable(jpeg_bytes)
        self.on_message = None  # callable(line)
        self.on_status = None   # callable(status): connecting/connected/reconnecting/disconnected
        self.video_enabled = False
        self.connect_Flag = False
        self._cmd_sock = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._last_rx = time.monotonic()

    def start(self, ip):
        self.stop()
        self._stop = threading.Event()
        threading.Thread(target=self._run, args=(ip, self._stop), daemon=True).start()

    def stop(self):
        self._stop.set()
        with self._send_lock:
            sock, self._cmd_sock = self._cmd_sock, None
        self.connect_Flag = False
        _close_socket(sock)

    def set_video(self, enabled):
        self.video_enabled = enabled
        self._last_rx = time.monotonic()
        self.sendData(f"{cmd.CMD_VIDEO}#{1 if enabled else 0}\n")

    def _emit_status(self, status, stop):
        if self.on_status and stop is self._stop:
            self.on_status(status)

    @staticmethod
    def _open(ip, port):
        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.settimeout(POLL_TIMEOUT)
        return sock

    def _run(self, ip, stop):
        self._emit_status('connecting', stop)
        while not stop.is_set():
            cmd_sock = video_sock = None
            try:
                cmd_sock = self._open(ip, CMD_PORT)
                video_sock = self._open(ip, VIDEO_PORT)
            except OSError as e:
                print(f"Connect to {ip} failed: {e}")
                _close_socket(cmd_sock)
                _close_socket(video_sock)
                stop.wait(RECONNECT_DELAY)
                continue

            dead = threading.Event()
            with self._send_lock:
                if stop.is_set():
                    _close_socket(cmd_sock)
                    _close_socket(video_sock)
                    break
                self._cmd_sock = cmd_sock
            self.connect_Flag = True
            print(f"Connected to {ip}")
            self._emit_status('connected', stop)
            # Re-apply video state; a click on "Open Video" before connecting would otherwise be lost.
            self.set_video(self.video_enabled)

            cmd_thread = threading.Thread(target=self._cmd_loop, args=(cmd_sock, stop, dead), daemon=True)
            cmd_thread.start()
            try:
                self._video_loop(video_sock, stop, dead)
            except (OSError, ValueError) as e:
                if not stop.is_set() and not dead.is_set():
                    print(f"Video connection lost: {e}")
            dead.set()
            self.connect_Flag = False
            with self._send_lock:
                if self._cmd_sock is cmd_sock:
                    self._cmd_sock = None
            _close_socket(cmd_sock)
            _close_socket(video_sock)
            cmd_thread.join(timeout=POLL_TIMEOUT * 2)
            if stop.is_set():
                break
            self._emit_status('reconnecting', stop)
            stop.wait(RECONNECT_DELAY)
        self._emit_status('disconnected', stop)

    def _recv_exact(self, sock, n, stop, dead):
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            if stop.is_set() or dead.is_set():
                raise ConnectionAbortedError("connection closed")
            try:
                r = sock.recv_into(view[got:], n - got)
            except socket.timeout:
                if self.video_enabled and time.monotonic() - self._last_rx > STALE_TIMEOUT:
                    raise TimeoutError(f"no video data for {STALE_TIMEOUT:.0f}s")
                continue
            if r == 0:
                raise ConnectionResetError("video connection closed by car")
            got += r
            self._last_rx = time.monotonic()
        return bytes(buf)

    def _video_loop(self, sock, stop, dead):
        while not stop.is_set() and not dead.is_set():
            length = struct.unpack('<L', self._recv_exact(sock, 4, stop, dead))[0]
            if not 0 < length <= MAX_FRAME_BYTES:
                # Stream framing is lost; only a fresh connection can resync it.
                raise ValueError(f"invalid frame length {length}")
            jpg = self._recv_exact(sock, length, stop, dead)
            if self.video_enabled and self.on_frame and is_valid_jpeg(jpg):
                self.on_frame(jpg)

    def _cmd_loop(self, sock, stop, dead):
        pending = ""
        next_power = 0.0
        last_rx = time.monotonic()
        try:
            while not stop.is_set() and not dead.is_set():
                now = time.monotonic()
                if now >= next_power:
                    self.sendData(f"{cmd.CMD_POWER}\n")
                    next_power = now + POWER_INTERVAL
                try:
                    data = sock.recv(1024)
                except socket.timeout:
                    if time.monotonic() - last_rx > CMD_STALE_TIMEOUT:
                        raise TimeoutError(f"no reply from car for {CMD_STALE_TIMEOUT:.0f}s")
                    continue
                if not data:
                    raise ConnectionResetError("command connection closed by car")
                last_rx = time.monotonic()
                pending += data.decode('utf-8', 'ignore')
                *lines, pending = pending.split('\n')
                for line in lines:
                    if line and self.on_message:
                        self.on_message(line)
        except OSError as e:
            if not stop.is_set() and not dead.is_set():
                print(f"Command connection lost: {e}")
        finally:
            dead.set()

    def sendData(self, s):
        with self._send_lock:
            sock = self._cmd_sock
            if sock is None:
                return False
            try:
                sock.sendall(s.encode('utf-8'))
                return True
            except OSError as e:
                print(f"Send failed: {e}")
                # Wake the command loop so the session reconnects.
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return False

if __name__ == '__main__':
    pass

