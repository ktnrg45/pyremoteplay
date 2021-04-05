"""Stream Packets for pyremoteplay."""
import json
import logging
from base64 import b64encode

from google.protobuf.message import DecodeError

from .takion_pb2 import *
from .util import log_bytes

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

STREAM_START = b'\x00\x00\x00\x40\x01\x00\x00'

class UnexpectedMessage(Exception):
    """Message not Expected."""
    pass


class UnexpectedData(Exception):
    """Data incorrect or not expected."""
    pass


LAUNCH_SPEC = {
    'sessionId': 'sessionId4321',
    'streamResolutions': [
        {
            'resolution': {
                'width': None,
                'height': None
            },
            'maxFps': None,
            'score': 10
        }
    ],
    'network': {
        'bwKbpsSent': None,
        'bwLoss': 0.001000,
        'mtu': None,
        'rtt': None,
        'ports': [53, 2053]
    },
    'slotId': 1,
    'appSpecification': {
        'minFps': 30,
        'minBandwidth': 0,
        'extTitleId': 'ps3',
        'version': 1,
        'timeLimit': 1,
        'startTimeout': 100,
        'afkTimeout': 100,
        'afkTimeoutDisconnect': 100},
    'konan': {
        'ps3AccessToken': 'accessToken',
        'ps3RefreshToken': 'refreshToken'},
    'requestGameSpecification': {
        'model': 'bravia_tv',
        'platform': 'android',
        'audioChannels': '5.1',
        'language': 'sp',
        'acceptButton': 'X',
        'connectedControllers': ['xinput', 'ds3', 'ds4'],
        'yuvCoefficient': 'bt601',
        'videoEncoderProfile': 'hw4.1',
        'audioEncoderProfile': 'audio1'
    },
    'userProfile': {
        'onlineId': 'psnId',
        'npId': 'npId',
        'region': 'US',
        'languagesUsed': ['en', 'jp']
    },
    'handshakeKey': None}


def get_launch_spec(
        handshake_key: bytes, resolution: dict, max_fps: int, rtt: int,
        mtu_in: int) -> bytes:
    r = resolution
    launch_spec = LAUNCH_SPEC
    launch_spec['streamResolutions'][0]['resolution']['width'] = r['width']
    launch_spec['streamResolutions'][0]['resolution']['height'] = r['height']
    launch_spec['streamResolutions'][0]['maxFps'] = max_fps
    launch_spec['network']['bwKbpsSent'] = r['bitrate']
    launch_spec['network']['mtu'] = mtu_in
    launch_spec['network']['rtt'] = rtt
    launch_spec['handshakeKey'] = b64encode(handshake_key).decode()
    launch_spec = json.dumps(launch_spec)
    _LOGGER.debug(
        "Length: %s, Launch Spec JSON: %s", len(launch_spec), launch_spec)
    launch_spec = launch_spec.replace(' ', '')  # minify
    launch_spec = launch_spec.replace(':0.001,', ':0.001000,' )  # Add three zeros
    _LOGGER.debug(
        "Length: %s, Launch Spec JSON: %s", len(launch_spec), launch_spec)
    launch_spec = launch_spec.encode()
    launch_spec = b''.join([launch_spec, b'\x00'])
    return launch_spec


def message():
    """Return New Protobuf message."""
    msg = TakionMessage()
    msg.ClearField('type')
    return msg


def get_payload_type(proto_msg) -> str:
    payload_type = proto_msg.type
    name = proto_msg.DESCRIPTOR.fields_by_name['type']\
        .enum_type.values_by_number[payload_type].name
    return name


class ProtoHandler():
    """Handler for Protobuf Messages."""

    def big_payload(
            client_version=7, session_key=b'', launch_spec=b'',
            encrypted_key=b'', ecdh_pub_key=None, ecdh_sig=None):
        """Big Payload."""
        msg = message()
        msg.type = msg.PayloadType.BIG
        msg.big_payload.client_version = client_version
        msg.big_payload.session_key = session_key
        msg.big_payload.launch_spec = launch_spec
        msg.big_payload.encrypted_key = encrypted_key
        if ecdh_pub_key is not None:
            msg.big_payload.ecdh_pub_key = ecdh_pub_key
        if ecdh_sig is not None:
            msg.big_payload.ecdh_sig = ecdh_sig
        data = msg.SerializeToString()
        return data

    def send_corrupt_frame(start: int, end: int):
        """Notify of corrupt or missing frame."""
        msg = message()
        msg.type = msg.PayloadType.CORRUPTFRAME
        msg.corrupt_payload.start = start
        msg.corrupt_payload.end = end
        data = msg.SerializeToString()
        return data

    def disconnect_payload():
        """Disconnect Payload."""
        reason = "Client Disconnecting".encode()
        msg = message()
        msg.type = msg.PayloadType.DISCONNECT
        msg.disconnect_payload.reason = reason
        data = msg.SerializeToString()
        return data

    def senkusha_echo(enable: bool):
        """Senkusha Echo Payload."""
        msg = message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.ECHO_COMMAND
        msg.senkusha_payload.echo_command.state = enable
        data = msg.SerializeToString()
        return data

    def senkusha_mtu(req_id: int, mtu_req: int):
        """Senkusha MTU Payload."""
        msg = message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.MTU_COMMAND
        msg.senkusha_payload.mtu_command.id = req_id
        msg.senkusha_payload.mtu_command.mtu_req = mtu_req
        data = msg.SerializeToString()
        return data

    def senkusha_mtu_client(
            state: bool, mtu_id: int, mtu_req: int,
            mtu_down: int):
        """Senkusha MTU Client Payload."""
        msg = message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command =\
            SenkushaPayload.Command.CLIENT_MTU_COMMAND
        msg.senkusha_payload.client_mtu_command.state = state
        msg.senkusha_payload.client_mtu_command.id = mtu_id
        msg.senkusha_payload.client_mtu_command.mtu_req = mtu_req
        msg.senkusha_payload.client_mtu_command.mtu_down = mtu_down
        data = msg.SerializeToString()
        return data

    def __init__(self, stream):
        self.stream = stream
        self._recv_bang = False
        self._recv_info = False

    def handle_exc(self, exc, message, p_type):
        raise exc(message)

    def _ack(self, msg, channel: int):
        chunk_flag = 1
        msg = msg.SerializeToString()
        log_bytes("Proto Send", msg)
        self.stream.send_data(msg, chunk_flag, channel, proto=True)

    def handle(self, data: bytes):
        if data == STREAM_START:
            _LOGGER.info("Stream Started")
            return
        msg = message()
        try:
            msg.ParseFromString(data)
        except DecodeError:
            _LOGGER.error("Protobuf Error with message: %s", data)
            raise ValueError
        p_type = get_payload_type(msg)
        _LOGGER.debug("RECV Payload Type: %s", p_type)
        # _LOGGER.debug(msg.ListFields())

        if p_type == 'STREAMINFO':
            if not self._recv_info:
                self._recv_info = True
                res = msg.stream_info_payload.resolution[0]
                v_header = res.video_header
                s_width = self.stream.resolution['width']
                s_height = self.stream.resolution['height']
                if s_width != res.width or s_height != res.height:
                    _LOGGER.warning("RECV Unexpected resolution: %s x %s", res.width, res.height)
                #     self.handle_exc(
                #         UnexpectedData,
                #         'Width: {}, Height: {}'.format(
                #             res.width, res.height))
                _LOGGER.debug("RECV Stream Info")
                self.parse_streaminfo(
                    msg.stream_info_payload, v_header)
            channel = 9
            msg = message()
            msg.type = msg.PayloadType.STREAMINFOACK
            self.stream._ready()
            self._ack(msg, channel)

        elif p_type == 'BANG' and not self._recv_bang:
            _LOGGER.debug("RECV Bang")
            accepted = True
            if not msg.bang_payload.version_accepted:
                _LOGGER.error("Version not accepted")
                accepted = False
            if not msg.bang_payload.encrypted_key_accepted:
                _LOGGER.error("Enrypted Key not accepted")
                accepted = False

            if accepted:
                self._recv_bang = True
                self.set_ciphers(msg.bang_payload)
            else:
                _LOGGER.error("RP Launch Spec not accepted")
                self.stream._stop_event.set()

        elif p_type == 'HEARTBEAT':
            channel = 1
            msg = message()
            msg.type = msg.PayloadType.HEARTBEAT
            self._ack(msg, channel)
            self.stream.controller.set_button('ps', True)  # ####
            self.stream.controller.set_button('ps', False)  # ####

        elif p_type == 'DISCONNECT':
            _LOGGER.info("Host Disconnected; Reason: %s", msg.disconnect_payload.reason)
            self.stream._stop_event.set()

    def set_ciphers(self, msg):
        """Set Ciphers."""
        if not self.stream._ecdh.set_secret(msg.ecdh_pub_key, msg.ecdh_sig):
            self.stream._stop_event.set()
        self.stream.cipher = self.stream._ecdh.init_ciphers()

    def parse_streaminfo(self, msg, video_header: bytes):
        self.video_header = video_header
        log_bytes("Video Header", video_header)
        self.audio_header = msg.audio_header
        log_bytes("Audio Header", msg.audio_header)
        self.start_timeout = msg.start_timeout
        self.afk_timeout = msg.afk_timeout
        self.afk_timeout_disconnect = msg.afk_timeout_disconnect
        self.congestion_control_interval = msg.congestion_control_interval
        self.stream.av.set_headers(video_header, msg.audio_header)
