# pylint: disable=no-member
"""Protobuf methods."""

import logging

from google.protobuf.message import DecodeError
from .takion_pb2 import SenkushaPayload, TakionMessage
from .util import log_bytes

_LOGGER = logging.getLogger(__name__)


class ProtoHandler:
    """Handler for Protobuf Messages."""

    @staticmethod
    def message():
        """Return New Protobuf message."""
        msg = TakionMessage()
        msg.ClearField("type")
        return msg

    @staticmethod
    def get_payload_type(proto_msg) -> str:
        """Return Payload type."""
        payload_type = proto_msg.type
        name = (
            proto_msg.DESCRIPTOR.fields_by_name["type"]
            .enum_type.values_by_number[payload_type]
            .name
        )
        return name

    @staticmethod
    def big_payload(
        client_version=7,
        session_key=b"",
        launch_spec=b"",
        encrypted_key=b"",
        ecdh_pub_key=None,
        ecdh_sig=None,
    ):
        """Big Payload."""
        msg = ProtoHandler.message()
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

    @staticmethod
    def send_corrupt_frame(start: int, end: int):
        """Notify of corrupt or missing frame."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.CORRUPTFRAME
        msg.corrupt_payload.start = start
        msg.corrupt_payload.end = end
        data = msg.SerializeToString()
        return data

    @staticmethod
    def disconnect_payload():
        """Disconnect Payload."""
        reason = "Client Disconnecting".encode()
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.DISCONNECT
        msg.disconnect_payload.reason = reason
        data = msg.SerializeToString()
        return data

    @staticmethod
    def senkusha_echo(enable: bool):
        """Senkusha Echo Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.ECHO_COMMAND
        msg.senkusha_payload.echo_command.state = enable
        data = msg.SerializeToString()
        return data

    @staticmethod
    def senkusha_mtu(req_id: int, mtu_req: int, num: int):
        """Senkusha MTU Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.MTU_COMMAND
        msg.senkusha_payload.mtu_command.id = req_id
        msg.senkusha_payload.mtu_command.mtu_req = mtu_req
        msg.senkusha_payload.mtu_command.num = num
        data = msg.SerializeToString()
        return data

    @staticmethod
    def senkusha_mtu_client(state: bool, mtu_id: int, mtu_req: int, mtu_down: int):
        """Senkusha MTU Client Payload."""
        msg = ProtoHandler.message()
        msg.type = msg.PayloadType.SENKUSHA
        msg.senkusha_payload.command = SenkushaPayload.Command.CLIENT_MTU_COMMAND
        msg.senkusha_payload.client_mtu_command.state = state
        msg.senkusha_payload.client_mtu_command.id = mtu_id
        msg.senkusha_payload.client_mtu_command.mtu_req = mtu_req
        msg.senkusha_payload.client_mtu_command.mtu_down = mtu_down
        data = msg.SerializeToString()
        return data

    def __init__(self, stream):
        self._stream = stream
        self._recv_bang = False
        self._recv_info = False

    def _ack(self, msg, channel: int):
        chunk_flag = 1
        msg = msg.SerializeToString()
        # log_bytes("Proto Send", msg)
        self._stream.send_data(msg, chunk_flag, channel, proto=True)

    def _parse_streaminfo(self, msg, video_header: bytes):
        info = {
            "video_header": video_header,
            "audio_header": msg.audio_header,
            "start_timeout": msg.start_timeout,
            "afk_timeout": msg.afk_timeout,
            "afk_timeout_disconnect": msg.afk_timeout_disconnect,
            "congestion_control_interval": msg.congestion_control_interval,
        }
        self._stream.recv_stream_info(info)

    def handle(self, data: bytes):
        """Handle message."""
        msg = ProtoHandler.message()
        try:
            msg.ParseFromString(data)
        except (DecodeError, RuntimeWarning) as error:
            log_bytes(f"Protobuf Error: {error}", data)
            return
        p_type = ProtoHandler.get_payload_type(msg)
        _LOGGER.debug("RECV Payload Type: %s", p_type)

        if p_type == "STREAMINFO":
            if not self._recv_info:
                self._recv_info = True
                res = msg.stream_info_payload.resolution[0]
                v_header = res.video_header
                _LOGGER.debug("RECV Stream Info")
                self._parse_streaminfo(msg.stream_info_payload, v_header)
            channel = 9
            msg = ProtoHandler.message()
            msg.type = msg.PayloadType.STREAMINFOACK
            self._ack(msg, channel)

        elif p_type == "BANG" and not self._recv_bang:
            _LOGGER.debug("RECV Bang")
            ecdh_pub_key = b""
            ecdh_sig = b""
            accepted = True
            if not msg.bang_payload.version_accepted:
                _LOGGER.error("Version not accepted")
                accepted = False
            if not msg.bang_payload.encrypted_key_accepted:
                _LOGGER.error("Enrypted Key not accepted")
                accepted = False
            if accepted:
                ecdh_pub_key = msg.bang_payload.ecdh_pub_key
                ecdh_sig = msg.bang_payload.ecdh_sig
                self._recv_bang = True
            self._stream.recv_bang(accepted, ecdh_pub_key, ecdh_sig)

        elif p_type == "BIG":
            return

        elif p_type == "HEARTBEAT":
            channel = 1
            msg = ProtoHandler.message()
            msg.type = msg.PayloadType.HEARTBEAT
            self._ack(msg, channel)

        elif p_type == "DISCONNECT":
            _LOGGER.info("Host Disconnected; Reason: %s", msg.disconnect_payload.reason)
            self._stream.stop_event.set()

        # Test Packets
        elif p_type == "SENKUSHA":
            if self._stream.is_test and self._stream.test:
                mtu_req = msg.senkusha_payload.mtu_command.mtu_req
                mtu_sent = msg.senkusha_payload.mtu_command.mtu_sent
                self._stream.test.recv_mtu_in(mtu_req, mtu_sent)
        else:
            _LOGGER.info("RECV Unhandled Payload Type: %s", p_type)
