import logging

import pyautogui as pag


class KeyboardEmulator:
    """Emulate keyboard presses."""
    valid_meta_keys = ['ctrl', 'alt', 'shift']

    def __init__(self):
        self.meta_key_combo = []

    def shout(self, value: str):
        if value in self.valid_meta_keys:
            self.handle_key_hold(value)
        else:
            self._shout(value)
            self.meta_key_combo = []

    def handle_key_hold(self, value: str):
        if value in self.meta_key_combo:
            self.meta_key_combo.remove(value)  # Release
        else:
            self.meta_key_combo.append(value)  # Press

    def _shout(self, value: str):
        with pag.hold(self.meta_key_combo):
            logging.debug(f"Keyboard out: {'+'.join(self.meta_key_combo)} {value}")
            if pag.isValidKey(value):
                pag.press(value)
            else:
                pag.write(str(value))
