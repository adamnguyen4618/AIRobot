from PyQt5.QtCore import QTimer

from Command import COMMAND as cmd

OUTER_SPEED = 4095
# Firmware bumps any nonzero speed up to 1600, so the inner side can only be 0 or >= 1600.
INNER_SPEED = 1600
# Fraction of time the inner wheels run at INNER_SPEED (rest is stopped): 0 pivots (tightest), higher widens the circle.
# Wheels coast during the off ticks, so the real inner speed is well above duty * INNER_SPEED; tune by feel.
INNER_DUTY = {-1: 0.0, 1: 0.05}
PULSE_MS = 50
# Time for roughly one full lap per direction; tune on the real car.
CIRCLE_DURATION_MS = {-1: 2230, 1: 1800}


class Circle:
    LEFT = -1
    RIGHT = 1

    def __init__(self, tcp, on_state_changed=None):
        self.tcp = tcp
        self.on_state_changed = on_state_changed  # called with (running, direction)
        self.running = False
        self.direction = Circle.LEFT
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.stop)
        self._pulse_timer = QTimer()
        self._pulse_timer.timeout.connect(self._pulse)
        self._acc = 0.0
        self._inner_on = False

    def toggle(self, direction):
        if self.running:
            self.stop()
        else:
            self.start(direction)

    def start(self, direction, duration_ms=None):
        self.direction = direction
        self._acc = 0.0
        self._inner_on = INNER_DUTY[direction] >= 1
        if not self._send_motors():
            return
        self._set_running(True)
        if 0 < INNER_DUTY[direction] < 1:
            self._pulse_timer.start(PULSE_MS)
        self._timer.start(duration_ms or CIRCLE_DURATION_MS[direction])

    def _send_motors(self):
        inner = INNER_SPEED if self._inner_on else 0
        left_wheels, right_wheels = (inner, OUTER_SPEED) if self.direction == Circle.LEFT else (OUTER_SPEED, inner)
        return self.tcp.sendData(f"{cmd.CMD_MOTOR}#{left_wheels}#{left_wheels}#{right_wheels}#{right_wheels}\n")

    def _pulse(self):
        # Error-diffusion keeps the inner wheels on for INNER_DUTY of the ticks, spread evenly.
        self._acc += INNER_DUTY[self.direction]
        inner_on = self._acc >= 1
        if inner_on:
            self._acc -= 1
        if inner_on != self._inner_on:
            self._inner_on = inner_on
            self._send_motors()

    def stop(self):
        self._timer.stop()
        self._pulse_timer.stop()
        if not self.running:
            return
        self.tcp.send_motor_stop()
        self._set_running(False)

    def _set_running(self, value):
        self.running = value
        if self.on_state_changed:
            self.on_state_changed(value, self.direction)
