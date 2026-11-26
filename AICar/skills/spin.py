from PyQt5.QtCore import QTimer

from Command import COMMAND as cmd

SPIN_SPEED = 1250
# Time for roughly one full turn per direction; the two sides differ, so tune each on the real car.
SPIN_DURATION_MS = {-1: 2475, 1: 1980}


class Spin:
    LEFT = -1
    RIGHT = 1

    def __init__(self, tcp, on_state_changed=None):
        self.tcp = tcp
        self.on_state_changed = on_state_changed  # called with (spinning, direction)
        self.spinning = False
        self.direction = Spin.LEFT
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.stop)

    def toggle(self, direction):
        if self.spinning:
            self.stop()
        else:
            self.start(direction)

    def start(self, direction, duration_ms=None):
        # Spin in place: left-side wheels forward and right-side backward turns right; reversed turns left.
        left_wheels = SPIN_SPEED * direction
        right_wheels = -left_wheels
        if not self.tcp.sendData(f"{cmd.CMD_MOTOR}#{left_wheels}#{left_wheels}#{right_wheels}#{right_wheels}\n"):
            return
        self.direction = direction
        self._set_spinning(True)
        self._timer.start(duration_ms or SPIN_DURATION_MS[direction])

    def stop(self):
        self._timer.stop()
        if not self.spinning:
            return
        self.tcp.sendData(f"{cmd.CMD_MOTOR}#0#0#0#0\n")
        self._set_spinning(False)

    def _set_spinning(self, value):
        self.spinning = value
        if self.on_state_changed:
            self.on_state_changed(value, self.direction)
