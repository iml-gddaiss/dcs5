"""
Author : JeromeJGuay
Date : May 2022

This module contains the Class relative to the DCS5_XT Board Controller and Client.


References
----------
    https://bigfinllc.com/wp-content/uploads/Big-Fin-Scientific-Fish-Board-Integration-Guide-V2_0.pdf?fbclid=IwAR0tJMwvN7jkqxgEhRQABS0W3HLLntpOflg12bMEwM5YrDOwcHStznJJNQM

"""

import logging
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass
from queue import Queue
from typing import *

import platform

import pyautogui as pag
pag.FAILSAFE = False

from dcs5.controller_configurations import load_config, ControllerConfiguration, ConfigError
from dcs5.devices_specification import load_devices_specification, DevicesSpecification
from dcs5.control_box_parameters import load_control_box_parameters, ControlBoxParameters

MONITORING_DELAY = 2 # WINDOWS ONLY

AFTER_SENT_SLEEP = 0.01

HANDLER_SLEEP = 0.01

LISTENER_SLEEP = 0.005

BOARD_MSG_ENCODING = 'UTF-8'
BUFFER_SIZE = 1024


def cycle(my_list: iter):
    index = 0
    while True:
        yield my_list[index]
        index = (index + 1) % len(my_list)


class ConnectionLost(Exception):
    pass


@dataclass
class InternalBoardState:
    sensor_mode: str = None
    stylus_status_msg: str = None
    stylus_settling_delay: int = None
    stylus_max_deviation: int = None
    number_of_reading: int = None

    battery_level: str = None
    board_stats: str = None
    board_interface: str = None
    calibrated: bool = None
    cal_pt_1: int = None
    cal_pt_2: int = None

    backlighting_level: int = None
    backlighting_auto_mode: bool = None
    backlighting_sensitivity: int = None


class Shouter:
    valid_meta_keys = ['ctrl', 'alt', 'shift']

    def __init__(self):
        self._with_control = False
        self._with_shift = False
        self._with_alt = False
        self.input: str = None

        self.meta_key_combo = []

    def shout(self, value: str):
        self.input = value
        if self.input in self.valid_meta_keys:
            if self.input in self.meta_key_combo:
                self.meta_key_combo.remove(self.input)
            else:
                self.meta_key_combo.append(self.input)
        else:
            self._shout()
            self._clear_meta_key_combo()

    def _clear_meta_key_combo(self):
        self.meta_key_combo = []

    def _shout(self):
        logging.error('Shouter _shout method not defined.')


class ServerInput(Shouter):
    """
    Inputs are queued in `inputs_queue`.
    Inputs are taken from the input_queue with the get() method.
    If more than 5 inputs are in the queue at one time, the queue is cleared.
    """

    def __init__(self):
        super().__init__()
        self.inputs_queue = queue.Queue(max_size=5)

    def _shout(self):
        _input = self.meta_key_combo + [self.input]
        try:
            self.inputs_queue.put_nowait(_input)
        except queue.Full:
            self.inputs_queue.queue.clear()

    def get(self):
        try:
            return self.inputs_queue.get()
        except queue.Empty:
            return None


class KeyboardInput(Shouter):
    """
    Emulate keyboard presses.
    """

    def __init__(self):
        super().__init__()

    def _shout(self):
        with pag.hold(self.meta_key_combo):
            logging.debug(f"Keyboard out: {'+'.join(self.meta_key_combo)} {self.input}")
            if pag.isValidKey(self.input):
                pag.press(self.input)
            else:
                pag.write(str(self.input))


class BluetoothClient:
    """
    RFCOMM ports goes from 1 to 30
    """
    min_port = 1
    max_port = 30
    reconnection_delay = 5

    def __init__(self):
        self._mac_address: str = None
        self._port: int = None
        self.socket: socket.socket = None
        self.default_timeout = 0.1
        self.isconnected = False

    def connect(self, mac_address: str = None, timeout: int = None):
        self._mac_address = mac_address
        timeout = timeout or self.default_timeout
        logging.debug(f'Attempting to connect to board. Timeout: {timeout} seconds')
        try:
            for port in range(self.min_port, self.max_port):  # check for all available ports
                self.socket = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                self.socket.settimeout(timeout)
                try:
                    logging.debug(f'port: {port}')
                    self.socket.connect((self._mac_address, port))
                    self._port = port
                    self.isconnected = True
                    logging.debug(f'Connected to port {self._port}')
                    logging.debug(f'Socket name: {self.socket.getsockname()}')
                    break
                except PermissionError:
                    logging.debug('Client.connect: PermissionError')
                    pass
                port += 1
            if not self.isconnected:
                logging.error('No available ports were found.')

        except OSError as err:
            self._process_os_error_code(err)
        finally:
            self.socket.settimeout(self.default_timeout)

    @property
    def mac_address(self):
        return self._mac_address

    @property
    def port(self):
        return self._port

    def send(self, command: str):
        try:
            self.socket.sendall(command.encode(BOARD_MSG_ENCODING))
        except OSError as err:
            logging.debug(f'OSError on sendall: {err.args}')

    def receive(self):
        try:
            return self.socket.recv(BUFFER_SIZE).decode(BOARD_MSG_ENCODING)
        except OSError as err:
            if err.errno is None:
                pass
            else:
                self._process_os_error_code(err)
                self.reconnect()
            return ""

    def reconnect(self):
        self.close()
        while not self.isconnected:
            logging.error('Attempting to reconnect.')
            self.connect(self.mac_address, timeout=30)
            time.sleep(5)
        if self.isconnected:
            logging.error('Board Reconnected')

    def clear(self):
        self.receive()

    def close(self):
        self.socket.close()
        self.isconnected = False

    def _process_os_error_code(self, err):
        match err.errno:
            case None:
                pass
            case 104:
                logging.error('Connection to device lost. (err104)')
            case 110:
                logging.error('Connection to device lost. (err110)')
            case 113:
                logging.error('Bluetooth turned off. Connection to device lost. (err113)')
            case 10022:
                logging.error('Bluetooth turned off.')
            case 10048:
                logging.error(f'Port {self.port} unavailable. (err10048)')
            case 10050:
                logging.error('Connection to device lost. (err10050)')
            case 10053:
                logging.error('Connection to device lost. (err10053)')
            case 10060:
                logging.error('Connection to device lost. (err10060')
            case 10064:
                logging.error(f'Port {self.port} unavailable. (err10064)')
            case _:
                logging.error(f'OSError (new): {err.errno}')


class Dcs5Controller:
    dynamic_stylus_settings: bool
    output_mode: str
    reading_profile: str
    length_units: str
    stylus: str
    stylus_offset: int
    stylus_cyclical_list: Generator

    def __init__(self, config_path: str, devices_specifications_path: str, control_box_parameters_path: str,
                 shouter='keyboard'):
        """

        Parameters
        ----------
        config_path
        devices_specifications_path
        control_box_settings_path
        """
        self.config_path = config_path
        self.devices_specifications_path = devices_specifications_path
        self.control_box_parameters_path = control_box_parameters_path
        self.config: ControllerConfiguration = None
        self.device_specs: DevicesSpecification = None
        self.control_box_parameters: ControlBoxParameters = None
        self._load_configs()

        self.listen_thread: threading.Thread = None
        self.command_thread: threading.Thread = None
        self.monitoring_thread: threading.Thread = None

        self.socket_listener = SocketListener(self)
        self.command_handler = CommandHandler(self)

        self.is_listening = False  # listening to the keyboard on the connected socket.
        self.is_muted = False  # Message are processed but keyboard input are suppress.

        self.ping_event_check = threading.Event()
        self.thread_barrier = threading.Barrier(2)

        self.client = BluetoothClient()

        self.internal_board_state = InternalBoardState()  # Board Current State
        self.set_board_settings()
        self.is_sync = False  # True if the Dcs5Controller board settings are the same as the Board Internal Settings.

        self.mappable_commands = {  # Item are callable methods.
            "BACKLIGHT_UP": self.backlight_up,
            "BACKLIGHT_DOWN": self.backlight_down,
            "CHANGE_STYLUS": self.cycle_stylus,
            "UNITS_mm": self.change_length_units_mm,
            "UNITS_cm": self.change_length_units_cm
        }

        if shouter not in ['keyboard', 'server']:
            logging.error('Invalid shouter method: Must be one of [keyboard, server]. Defaulting to keyboard.')
            shouter = 'keyboard'

        if shouter == 'keyboard':
            self.shouter = KeyboardInput()
        else:
            self.shouter = ServerInput()

    def _load_configs(self):
        control_box_parameters = load_control_box_parameters(self.control_box_parameters_path)
        devices_spec = load_devices_specification(self.devices_specifications_path)
        config = load_config(self.config_path)

        if control_box_parameters is None:
            raise ConfigError(f'Error in {self.control_box_parameters_path}. File could not be loaded.')
        if devices_spec is None:
            raise ConfigError(f'Error in {self.devices_specifications_path}. File could not be loaded.')
        if config is None:
            raise ConfigError(f'Error in {self.config_path}. File could not be loaded.')

        self.control_box_parameters = control_box_parameters
        self.devices_spec = devices_spec
        self.config = validate_config(config, self.control_box_parameters)

    def reload_configs(self):
        self.is_sync = False
        self._load_configs()

    def set_board_settings(self):
        self.dynamic_stylus_settings = self.config.launch_settings.dynamic_stylus_mode
        self.output_mode = self.config.launch_settings.output_mode
        self.reading_profile = self.config.launch_settings.reading_profile
        self.length_units = self.config.launch_settings.length_units
        self.stylus: str = self.config.launch_settings.stylus
        self.stylus_offset = self.devices_spec.stylus_offset[self.stylus]
        self.stylus_cyclical_list = cycle(list(self.devices_spec.stylus_offset.keys()))

    def start_client(self, mac_address: str = None):
        """Create a socket and tries to connect with the board."""
        if self.client.isconnected:
            logging.debug("Client Already Connected.")
        else:
            mac_address = mac_address or self.config.client.mac_address
            self.client.connect(mac_address, timeout=30)

    def close_client(self):
        if self.client.isconnected:
            self.stop_listening()
            self.client.close()
            logging.info('Client Closed.')
        else:
            logging.info('Client Already Closed')

    def restart_client(self):
        self.close_client()
        time.sleep(0.5)
        was_listening = self.is_listening
        try:
            self.start_client()
        except OSError as err:
            logging.error(f'Failed to Start_Client. Trying again ...')
            logging.debug(f'restart_client OSError: {str(err)}')
            self.restart_client()
            if was_listening:
                self.start_listening()

    def start_listening(self):
        logging.debug(f"Active Threads: {threading.enumerate()}")
        if not self.is_listening:
            logging.debug('Starting Threads.')
            self.is_listening = True
            self.command_thread = threading.Thread(target=self.command_handler.processes_queues, name='command',
                                                   daemon=True)
            self.command_thread.start()

            self.listen_thread = threading.Thread(target=self.socket_listener.listen, name='listen', daemon=True)
            self.listen_thread.start()

            if platform.system() == 'Windows':
                self.monitoring_thread = threading.Thread(target=self.monitor_connection, name='listen', daemon=True)
                self.monitoring_thread.start()

        logging.info('Board is Active.')

    def stop_listening(self):
        if self.is_listening:
            self.is_listening = False
            time.sleep(self.client.socket.gettimeout())
            logging.debug("Listening stopped.")
            logging.debug("Queues and Socket Buffer Cleared.")
        logging.info('Board is Inactive.')

    def restart_listening(self):
        self.stop_listening()
        self.start_listening()

    def monitor_connection(self):
        "This is to raise a connection OSError if the connection is lost."
        while self.is_listening:
            self.client.send(" ") # a space is not a recognized command. Thus nothing is return.
            time.sleep(MONITORING_DELAY)

    def unmute_board(self):
        """Unmute board shout output"""
        if self.is_muted:
            self.is_muted = False
            logging.debug('Board unmuted')

    def mute_board(self):
        """Mute board shout output"""
        if not self.is_muted:
            self.is_muted = True
            logging.debug('Board muted')

    def init_controller_and_board(self):
        """Init measuring board.
        """
        logging.debug('Initiating Board.')
        time.sleep(1)  # Wait 1 second to give time to the socket buffer to be cleared.

        self.internal_board_state = InternalBoardState()
        self.is_sync = False
        logging.debug('Internal Board State Values cleared. is_sync set to False')

        was_listening = self.is_listening
        self.restart_listening()

        self.c_set_backlighting_level(0)

        reading_profile = self.config.reading_profiles[
            self.config.output_modes.mode_reading_profiles[self.output_mode]
        ]
        # SET DEFAULT VALUES
        self.c_set_interface(1)
        self.c_set_sensor_mode(0)
        self.c_set_stylus_detection_message(False)

        # SET USER VALUES
        self.c_set_stylus_settling_delay(reading_profile.settling_delay)
        self.c_set_stylus_max_deviation(reading_profile.max_deviation)
        self.c_set_stylus_number_of_reading(reading_profile.number_of_reading)
        self.c_set_backlighting_level(self.config.launch_settings.backlighting_level)
        self.c_set_backlighting_sensitivity(self.config.launch_settings.backlighting_sensitivity)
        self.c_set_backlighting_auto_mode(self.config.launch_settings.backlighting_auto_mode)

        self.c_check_calibration_state()

        if self.wait_for_ping() is True:
            if (
                    self.internal_board_state.sensor_mode == "length" and
                    self.internal_board_state.stylus_status_msg == "disable" and
                    self.internal_board_state.stylus_settling_delay == reading_profile.settling_delay and
                    self.internal_board_state.stylus_max_deviation == reading_profile.max_deviation and
                    self.internal_board_state.number_of_reading == reading_profile.number_of_reading
            ):
                self.is_sync = True
                logging.debug("Board initiation succeeded.")
            else:
                logging.debug("Board initiation failed.")
                state = [
                    self.internal_board_state.sensor_mode,
                    self.internal_board_state.stylus_status_msg,
                    (self.internal_board_state.stylus_settling_delay, reading_profile.settling_delay),
                    (self.internal_board_state.stylus_max_deviation, reading_profile.max_deviation),
                    (self.internal_board_state.number_of_reading, reading_profile.number_of_reading)]
                logging.debug(str(state))
        else:
            logging.debug("Ping was not received. Board initiation failed.")

        if not was_listening:
            self.stop_listening()

    def wait_for_ping(self, timeout=2):
        self.c_ping()
        self.ping_event_check.clear()
        logging.debug('Waiting for ping event.')
        return self.ping_event_check.wait(timeout)

    def calibrate(self, pt: int) -> int:
        """

        Parameters
        ----------
        pt

        Returns
        -------
        1 for good calibration
        0 for failed calibration
        """

        logging.info("Calibration Mode Enable.")

        was_listening = self.is_listening

        self.stop_listening()

        self.client.clear()
        self.client.send(f"&{pt}r#")
        self.client.socket.settimeout(5)
        msg = self.client.receive()
        self.stop_listening()
        try:
            if f'&Xr#: X={pt}\r' in msg:
                pt_value = self.internal_board_state.__dict__[f"cal_pt_{pt}"]
                logging.info(f"Calibration for point {pt}. Set stylus down at {pt_value} mm ...")
                while f'&{pt}c' not in msg:
                    msg += self.client.receive()
                logging.info(f'Point {pt} calibrated.')
                return 1
            else:
                return 0
        except KeyError:
            logging.info('Calibration Failed.')
            return 0
        finally:
            self.client.socket.settimeout(self.client.default_timeout)
            if was_listening:
                self.start_listening()

    def change_length_units_mm(self):
        self.length_units = "mm"
        logging.info(f"Length Units Change to mm")
        if self.client.isconnected:
            self.flash_lights(2, interval=.25)

    def change_length_units_cm(self):
        self.length_units = "cm"
        logging.info(f"Length Units Change to cm")
        if self.client.isconnected:
            self.flash_lights(2, interval=.25)

    def change_stylus(self, value: str):
        """Stylus must be one of [pen, finger]"""
        self.stylus = value
        self.stylus_offset = self.devices_spec.stylus_offset[self.stylus]
        logging.info(f'Stylus set to {self.stylus}. Stylus offset {self.stylus_offset}')
        if self.client.isconnected:
            self.flash_lights(2, interval=.25)

    def cycle_stylus(self):
        self.change_stylus(next(self.stylus_cyclical_list))

    def change_board_output_mode(self, value: str):
        """
        value must be one of  [length, bottom, top]
        """
        self.output_mode = value
        if self.client.isconnected:
            if self.output_mode == 'bottom':
                self.flash_lights(2, interval=.25)
            elif self.output_mode == 'top':
                self.flash_lights(2, interval=.25)
            else:
                self.flash_lights(2, interval=.25)

            if self.dynamic_stylus_settings is True:
                reading_profile = self.config.reading_profiles[
                    self.config.output_modes.mode_reading_profiles[self.output_mode]
                ]
                self.c_set_stylus_settling_delay(reading_profile.settling_delay)
                self.c_set_stylus_max_deviation(reading_profile.max_deviation)
                self.c_set_stylus_number_of_reading(reading_profile.number_of_reading)
        logging.debug(f'Board entry: {self.output_mode}.')

    def backlight_up(self):
        if self.internal_board_state.backlighting_level < self.control_box_parameters.max_backlighting_level:
            self.internal_board_state.backlighting_level += 25
            if self.internal_board_state.backlighting_level > self.control_box_parameters.max_backlighting_level:
                self.internal_board_state.backlighting_level = self.control_box_parameters.max_backlighting_level
            self.c_set_backlighting_level(self.internal_board_state.backlighting_level)
        else:
            logging.info("Backlighting is already at maximum.")

    def backlight_down(self):
        if self.internal_board_state.backlighting_level > 0:
            self.internal_board_state.backlighting_level += -25
            if self.internal_board_state.backlighting_level < 0:
                self.internal_board_state.backlighting_level = 0
            self.c_set_backlighting_level(self.internal_board_state.backlighting_level)
        else:
            logging.info("Backlighting is already at minimum.")

    def flash_lights(self, period: int, interval: int):
        current_backlight_level = self.internal_board_state.backlighting_level
        for i in range(period):
            self.c_set_backlighting_level(0)
            time.sleep(interval / 2)
            self.c_set_backlighting_level(current_backlight_level)
            time.sleep(interval / 2)

    def shout(self, value: Union[int, float, str]):
        if not self.is_muted:
            logging.debug(f"Shouted value {value}")
            self.shouter.shout(value)

    # command removed for safety reason.
    #def c_board_initialization(self):
    #    self.command_handler.queue_command("&init#", "Setting EEPROM init flag.\r")
    #    time.sleep(1)
    #    self.close_client()

    def c_ping(self):
        """This could use for something more useful. Like checking at regular interval if the board is still active:
        """
        self.command_handler.queue_command("a#", "%a:e#")

    def c_get_board_stats(self):
        self.command_handler.queue_command("b#", "regex_%b.*#")

    def c_get_battery_level(self):
        self.command_handler.queue_command('&q#', "regex_%q:.*#")

    def c_set_sensor_mode(self, value):
        """ 'length', 'alpha', 'shortcut', 'numeric' """
        self.command_handler.queue_command(
            f'&m,{value}#', ['length mode activated\r', 'alpha mode activated\r',
                             'shortcut mode activated\r', 'numeric mode activated\r'][value]
        )

    def c_set_interface(self, value: int):
        """
        FEED seems to enable box key strokes.
        """
        self.command_handler.queue_command(f"&fm,{value}#", None)
        if value == 0:
            self.internal_board_state.board_interface = "DCSLinkstream"
            logging.info(f'Interface set to {self.internal_board_state.board_interface}')
        elif value == 1:
            self.internal_board_state.board_interface = "FEED"
            logging.info(f'Interface set to {self.internal_board_state.board_interface}')

    def c_set_backlighting_level(self, value: int):
        if 0 <= value <= self.control_box_parameters.max_backlighting_level:
            self.command_handler.queue_command(f'&o,{value}#', None)
            self.internal_board_state.backlighting_level = value
        else:
            logging.warning(f"Backlighting level range: (0, {self.control_box_parameters.max_backlighting_level})")

    def c_set_backlighting_auto_mode(self, value: bool):
        self.command_handler.queue_command(f"&oa,{int(value)}", None)
        self.internal_board_state.backlighting_auto_mode = {True: 'auto', False: 'manual'}

    def c_set_backlighting_sensitivity(self, value: int):
        if 0 <= value <= self.control_box_parameters.max_backlighting_sensitivity:
            self.command_handler.queue_command(f"&os,{value}", None)
            self.internal_board_state.backlighting_sensitivity = value
        else:
            logging.warning(
                f"Backlighting sensitivity range: (0, {self.control_box_parameters.max_backlighting_sensitivity})")

    def c_set_stylus_detection_message(self, value: bool):
        """
        When disabled (false): %t0 %t1 are not sent
        """
        self.command_handler.queue_command(f'&sn,{int(value)}#', f'%sn:{int(value)}#\r')

    def c_set_stylus_settling_delay(self, value: int = 1):
        if self.control_box_parameters.min_settling_delay <= value <= self.control_box_parameters.max_settling_delay:
            self.command_handler.queue_command(f"&di,{value}#", f"%di:{value}#\r")
        else:
            logging.warning(
                f"Settling delay value range: ({self.control_box_parameters.min_settling_delay}, {self.control_box_parameters.max_settling_delay})")

    def c_set_stylus_max_deviation(self, value: int):
        if self.control_box_parameters.min_max_deviation <= value <= self.control_box_parameters.max_max_deviation:
            self.command_handler.queue_command(f"&dm,{value}#", f"%dm:{value}#\r")
        else:
            logging.warning(
                f"Settling delay value range: ({self.control_box_parameters.min_max_deviation}, {self.control_box_parameters.max_max_deviation})")

    def c_set_stylus_number_of_reading(self, value: int = 5):
        self.command_handler.queue_command(f"&dn,{value}#", f"%dn:{value}#\r")

    def c_restore_cal_data(self):
        self.command_handler.queue_command("&cr,m1,m2,raw1,raw2#", None)

    def _clear_cal_data(self):
        self.command_handler.queue_command("&ca#", None)
        self.calibrated = False

    def c_check_calibration_state(self):
        self.command_handler.queue_command('&u#', 'regex_%u:\d#\r')

    def c_set_calibration_points_mm(self, pt: int, pos: int):
        self.command_handler.queue_command(f'&{pt}mm,{pos}#', f'Cal Pt {pt} set to: {pos}\r')


class CommandHandler:
    def __init__(self, controller: Dcs5Controller):
        self.controller = controller

        self.send_queue = Queue()
        self.received_queue = Queue()
        self.expected_message_queue = Queue()

    def clear_queues(self):
        self.send_queue.queue.clear()
        self.received_queue.queue.clear()
        self.expected_message_queue.queue.clear()
        logging.debug("Handler Queues Cleared.")

    def processes_queues(self):
        self.clear_queues()
        self.controller.thread_barrier.wait()
        logging.debug('Command Handling Started')
        while self.controller.is_listening:
            if not self.received_queue.empty():
                self._validate_commands()
            if not self.send_queue.empty():
                self._send_command()
                time.sleep(AFTER_SENT_SLEEP)
            time.sleep(HANDLER_SLEEP)
        logging.debug('Command Handling Stopped')

    def _validate_commands(self):
        command_is_valid = False
        received = self.received_queue.get()
        expected = self.expected_message_queue.get()
        logging.debug(f'Received: {[received]}, Expected: {[expected]}')
        if "regex_" in expected:
            match = re.findall("(" + expected.strip('regex_') + ")", received)
            if len(match) > 0:
                command_is_valid = True
        elif received == expected:
            command_is_valid = True

        if command_is_valid:
            self._process_valid_commands(received)
        else:
            logging.error(f'Invalid: Command received: {[received]}, Command expected: {[expected]}')

    def _process_valid_commands(self, received):
        logging.debug('Command Valid')
        if 'mode activated' in received:
            for i in ["length", "alpha", "shortcut", "numeric"]:
                if i in received:
                    self.controller.internal_board_state.sensor_mode = i

            logging.debug(f'{received}')

        elif "a:e" in received:
            self.controller.ping_event_check.set()
            logging.debug('Ping is set.')

        elif "sn" in received:
            match = re.findall(f"%sn:(\d)#\r", received)
            if len(match) > 0:
                if match[0] == "1":
                    self.controller.internal_board_state.stylus_status_msg = "enable"
                    logging.info('Stylus Status Message Enable')
                else:
                    self.controller.internal_board_state.stylus_status_msg = "disable"
                    logging.info('Stylus Status Message Disable')

        elif "di" in received:
            match = re.findall(f"%di:(\d+)#\r", received)
            if len(match) > 0:
                self.controller.internal_board_state.stylus_settling_delay = int(match[0])
                logging.info(f"Stylus settling delay set to {match[0]}")

        elif "dm" in received:
            match = re.findall(f"%dm:(\d+)#\r", received)
            if len(match) > 0:
                self.controller.internal_board_state.stylus_max_deviation = int(match[0])
                logging.info(f"Stylus max deviation set to {int(match[0])}")

        elif "dn" in received:
            match = re.findall(f"%dn:(\d+)#\r", received)
            if len(match) > 0:
                self.controller.internal_board_state.number_of_reading = int(match[0])
                logging.info(f"Stylus number set to {int(match[0])}")

        elif "%b" in received:
            match = re.findall("%b:(.*)#", received)
            if len(match) > 0:
                logging.info(f'Board State: {match[0]}')
                self.controller.internal_board_state.board_stats = match[0]

        elif "%q" in received:
            match = re.findall("%q:(-*\d*,\d*)#", received)
            if len(match) > 0:
                logging.info(f'Battery level: {match[0]}')
                self.controller.internal_board_state.battery_level = match[0]

        elif "%u:" in received:
            match = re.findall("%u:(\d)#", received)
            if len(match) > 0:
                if match[0] == '0':
                    logging.info('Board is not calibrated.')
                    self.controller.internal_board_state.calibrated = False
                elif match[0] == '1':
                    logging.info('Board is calibrated.')
                    self.controller.internal_board_state.calibrated = True
            else:
                logging.error(f'Calibration state {self.controller.client.buffer}')

        elif 'Cal Pt' in received:
            logging.debug(received.strip("\r") + " mm")
            match = re.findall("Cal Pt (\d+) set to: (\d+)", received)
            if len(match) > 0:
                self.controller.internal_board_state.__dict__[f'cal_pt_{match[0][0]}'] = int(match[0][1])

    def queue_command(self, command, message=None):
        if message is not None:
            self.expected_message_queue.put(message)
        self.send_queue.put(command)
        logging.debug(f'Queuing: Command -> {[command]}, Expected -> {[message]}')

    def _send_command(self):
        command = self.send_queue.get()
        self.controller.client.send(command)
        logging.debug(f'Command Sent: {[command]}')


class SocketListener:
    def __init__(self, controller: Dcs5Controller):
        self.controller = controller
        self.message_queue = Queue()
        self.swipe_triggered = False
        self.with_ctrl = False
        self.buffer = ""
        self.last_input = None
        self.last_command = None

    def pop(self, i=None):
        """Return and clear the client buffer."""
        i = len(self.buffer) if i is None else i
        out, self.buffer = self.buffer[:i], self.buffer[i:]
        return out

    def listen(self):
        self.controller.client.clear()
        self.message_queue.queue.clear()
        logging.debug("Listener Queue and Client Buffers Cleared.")
        self.controller.thread_barrier.wait()
        logging.debug('Listener Queue cleared & Client Buffer Clear.')
        logging.debug('Listening started')
        while self.controller.is_listening:
            self.buffer += self.controller.client.receive()
            if len(self.buffer) > 0:
                logging.debug(f'Raw Buffer: {[self.buffer]}')
                self._split_board_message()
                self._process_board_message()
            time.sleep(LISTENER_SLEEP)
        logging.debug('Listening stopped')

    def _split_board_message(self):
        delimiters = ["\n", "\r", "#"]
        for d in delimiters:
            msg = self.buffer.split(d, 1)
            if len(msg) > 1:
                self.message_queue.put(msg[0]+d)
                self.buffer = msg[1]
                break

    def _process_board_message(self):
        """ANALYZE SOLICITED VS UNSOLICITED MESSAGE"""
        while not self.message_queue.empty():
            message = self.message_queue.get()
            logging.debug(f'Received Message: {message}')
            out_value = None
            msg_type, msg_value = self._decode_board_message(message)
            logging.debug(f"Message Type: {msg_type}, Message Value: {msg_value}")
            if msg_type == "controller_box_key":
                control_box_key = self.controller.devices_spec.control_box.keys_layout[msg_value]
                self.last_input = control_box_key
                out_value = self.controller.config.key_maps.control_box[control_box_key]

            elif msg_type == 'swipe':
                self.swipe_value = msg_value
                if msg_value > self.controller.config.output_modes.swipe_threshold:
                    self.swipe_triggered = True

            elif msg_type == 'length':
                if self.swipe_triggered is True:
                    self._check_for_stylus_swipe(msg_value)
                else:
                    out_value = self._map_board_length_measurement(msg_value)
            elif msg_type == "solicited":
                self.controller.command_handler.received_queue.put(msg_value)

            if out_value is not None:
                self.last_command = out_value
                self._process_output(out_value)

    @staticmethod
    def _decode_board_message(value: str):
        pattern = "%t,([0-9])#|%l,([0-9]*)#|%s,([0-9]*)#|F,([0-9]{2})#"
        match = re.findall(pattern, value)
        if len(match) > 0:
            if match[0][1] != "":
                return 'length', int(match[0][1])
            elif match[0][2] != "":
                return 'swipe', int(match[0][2])
            elif match[0][3] != "":
                return 'controller_box_key', match[0][3]
        else:
            return 'solicited', value

    def _process_output(self, value):
        if isinstance(value, list):
            for _value in value:
                self._process_output(_value)
        else:
            if value in self.controller.mappable_commands:
                self.controller.mappable_commands[value]()
            else:
                self.controller.shout(value)

    def _map_board_length_measurement(self, value: int):
        if self.controller.output_mode == 'length':
            out_value = value - self.controller.stylus_offset
            if self.controller.length_units == 'cm':
                out_value /= 10
            self.last_input = out_value
            return out_value
        else:
            index = int(
                (value - self.controller.devices_spec.board.relative_zero)
                / self.controller.devices_spec.board.key_to_mm_ratio
            )
            if index < self.controller.devices_spec.board.number_of_keys:
                board_key = self.controller.devices_spec.board.keys_layout[self.controller.output_mode][index]
                self.last_input = board_key
                return self.controller.config.key_maps.board[board_key]

    def _check_for_stylus_swipe(self, value: str):
        self.swipe_triggered = False
        self.last_input = "swipe"
        segments_limits = self.controller.config.output_modes.segments_limits
        if value <= segments_limits[-1]:
            for l_max, l_min, mode in zip(segments_limits[1:],
                                          segments_limits[:-1],
                                          self.controller.config.output_modes.segments_mode):
                if l_max >= int(value) > l_min:
                    self.controller.change_board_output_mode(mode)
                    break


def validate_config(config: ControllerConfiguration, control_box: ControlBoxParameters):
    if not 0 <= config.launch_settings.backlighting_level <= control_box.max_backlighting_level:
        raise ConfigError(f'launch_settings/Backlight_level outside range {(0, control_box.max_backlighting_level)}')

    for key, item in config.reading_profiles.items():
        if not control_box.min_settling_delay <= item.settling_delay <= control_box.max_settling_delay:
            raise ConfigError(
                f'reading_profiles/{key}/settling_delay outside range {(control_box.min_settling_delay, control_box.max_settling_delay)}')
        if not control_box.min_max_deviation <= item.max_deviation <= control_box.max_max_deviation:
            raise ConfigError(
                f'reading_profiles/{key}/max_deviation outside range {(control_box.min_max_deviation, control_box.max_max_deviation)}')
    return config
