"""Stream for pyremoteplay."""
import asyncio
import base64
import logging
import socket
import threading
import time
from struct import pack_into

from Cryptodome.Random import get_random_bytes
from Cryptodome.Util.strxor import strxor

from .crypt import StreamECDH
from .stream_packets import (Chunk, CongestionPacket, FeedbackPacket, Header,
                             Packet, ProtoHandler, get_launch_spec)
from .util import listener, log_bytes

_LOGGER = logging.getLogger(__name__)

STREAM_PORT = 9296
TEST_STREAM_PORT = 9297
A_RWND = 0x019000
OUTBOUND_STREAMS = 0x64
INBOUND_STREAMS = 0x64

DEFAULT_RTT = 1
DEFAULT_MTU = 1454
MIN_MTU = 576
UDP_IPV4_SIZE = 28

DATA_LENGTH = 26
DATA_ACK_LENGTH = 29


class RPStream():
    """RP Stream Class."""

    STATE_INIT = "init"
    STATE_READY = "ready"

    class Protocol(asyncio.Protocol):

        def __init__(self, stream):
            self.transport = None
            self.stream = stream

        def connection_made(self, transport):
            _LOGGER.debug("Connected Stream")
            self.transport = transport

        def datagram_received(self, data, addr):
            self.stream._handle(data)

        def sendto(self, data, addr):
            self.transport.sendto(data, addr)

        def close(self):
            self.transport.close()

    def __init__(self, session, stop_event, rtt=None, mtu=None, is_test=False, cb_stop=None):
        self._host = session.host
        self._port = STREAM_PORT if not is_test else TEST_STREAM_PORT
        self._session = session
        self._is_test = is_test
        self._test = StreamTest(self) if is_test else None
        self._state = None
        self._tsn = self._tag_local = 1  #int.from_bytes(get_random_bytes(4), "big")
        self._tag_remote = 0
        self._key_pos = 0
        self._protocol = None
        self._stop_event = stop_event
        self._worker = None
        self._cb_stop = cb_stop
        self._cb_ack = None
        self._cb_ack_tsn = 0
        self._ecdh = None
        self._verify_gmac = False
        self.cipher = None
        self.proto = ProtoHandler(self)
        self.av_handler = session.av_handler
        self.resolution = session.resolution
        self.max_fps = session.fps
        self.rtt = rtt if rtt is not None else DEFAULT_RTT
        self.mtu = mtu if mtu is not None else DEFAULT_MTU
        self.stream_info = None
        self.controller = None

    def connect(self):
        """Connect socket to Host."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        # Could be set if we're dropping packets
        # because they are overflowing the socket buffer
        # sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, bytes(A_RWND))
        self._protocol = sock
        self._state = RPStream.STATE_INIT
        self._worker = threading.Thread(
            target=listener,
            args=("Stream", self._protocol, self._handle, self._stop_event),
        )
        self._worker.start()
        self._send_init()

    async def async_connect(self):
        """Connect Async."""
        _, self._protocol = await self._session.loop.create_datagram_endpoint(lambda: RPStream.Protocol(self), local_addr=("0.0.0.0", 0))
        self._send_init()

    def ready(self):
        _LOGGER.info("Stream Ready")
        self._state = RPStream.STATE_READY

    def _advance_sequence(self):
        """Advance SCTP sequence number."""
        if self.state == RPStream.STATE_INIT:
            return
        self._tsn += 1

    def _send_init(self):
        """Send Init Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.INIT, tag=self._tag_local, tsn=self._tsn)
        self.send(msg.bytes())

    def _send_cookie(self, data: bytes):
        """Send Cookie Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.COOKIE, tag=self._tag_local, tag_remote=self._tag_remote, data=data)
        self.send(msg.bytes())

    def send_data(self, data: bytes, flag: int, channel: int, proto=False):
        """Send Data Packet."""
        advance_by = 0
        if self.cipher:
            self._advance_sequence()
            if proto:
                advance_by = len(data)

        msg = Packet(Header.Type.CONTROL, Chunk.Type.DATA, tag_remote=self._tag_remote, tsn=self._tsn, flag=flag, channel=channel, data=data)
        self.send(msg.bytes(self.cipher, False, advance_by))

    def _send_data_ack(self, ack_tsn: int):
        """Send Data Packet."""
        msg = Packet(Header.Type.CONTROL, Chunk.Type.DATA_ACK, tag_remote=self._tag_remote, tag=self._tag_local, tsn=ack_tsn)
        self.send(msg.bytes(self.cipher, False, DATA_ACK_LENGTH))

    def send_feedback(self, feedback_type: int, sequence: int, data=b'', state=None):
        """Send feedback packet."""
        msg = FeedbackPacket(feedback_type, sequence=sequence, data=data, state=state)
        self.send(msg.bytes(self.cipher, True))

    def send_congestion(self, received: int, lost: int):
        """Send congestion Packet."""
        msg = CongestionPacket(received=received, lost=lost)
        _LOGGER.info(msg)
        self.send(msg.bytes(self.cipher))

    def send(self, msg: bytes):
        """Send Message."""
        log_bytes(f"Stream Send", msg)
        self._protocol.sendto(msg, (self._host, self._port))

    def _handle(self, msg):
        """Handle packets."""
        av_type = Packet.is_av(msg[:1])
        if av_type:
            if self.av_handler.has_receiver and not self._is_test:
                self.av_handler.add_packet(msg)
            elif self._is_test and self._test:
                if av_type == Header.Type.AUDIO:
                    self._test.recv_rtt()
                else:
                    self._test.recv_mtu(msg)
        else:
            if not self.av_handler.has_receiver:
                self._handle_later(msg)
            else:
                # Run in Executor if processing av.
                self._session.sync_run_io(self._handle_later, msg)

    def _handle_later(self, msg):
        packet = Packet.parse(msg)
        _LOGGER.debug(packet)
        log_bytes(f"Stream RECV", msg)
        if self.cipher:
            gmac = packet.header.gmac
            _gmac = int.to_bytes(gmac, 4, "big")
            key_pos = packet.header.key_pos
            packet.header.gmac = packet.header.key_pos = 0
            if self._verify_gmac:
                gmac = self.cipher.verify_gmac(packet.bytes(), key_pos, _gmac)

        if packet.chunk.type == Chunk.Type.INIT_ACK:
            self._recv_init(packet)
        elif packet.chunk.type == Chunk.Type.COOKIE_ACK:
            self._recv_cookie_ack()
        elif packet.chunk.type == Chunk.Type.DATA_ACK:
            self._recv_data_ack(packet)
        elif packet.chunk.type == Chunk.Type.DATA:
            self._recv_data(packet)

    def _recv_init(self, packet):
        """Handle Init."""
        params = packet.params
        self._tag_remote = params["tag"]
        self._send_cookie(params["data"])

    def _recv_cookie_ack(self):
        """Handle Cookie Ack"""
        self._send_big()

    def _recv_data(self, packet):
        """Handle Data."""
        params = packet.params
        self._send_data_ack(params["tsn"])
        self.proto.handle(params["data"])

    def _recv_data_ack(self, packet):
        """Handle data ack."""
        params = packet.params
        _LOGGER.debug(f"TSN={params['tsn']} GAP_ACKs={params['gap_ack_blocks_count']} DUP_TSNs={params['dup_tsns_count']}")

        if self._cb_ack and self._cb_ack_tsn == params['tsn']:
            _LOGGER.debug("Received waiting TSN ACK")
            self._cb_ack()
            self._cb_ack = None
            self._cb_ack_tsn = 0

    def _send_big(self):
        chunk_flag = channel = 1
        if not self._is_test:
            self._ecdh = StreamECDH()
            launch_spec = self._format_launch_spec(self._ecdh.handshake_key)
            encrypted_key = bytes(4)
            ecdh_pub_key = self._ecdh.public_key
            ecdh_sig = self._ecdh.public_sig
            client_version = 9
        else:
            launch_spec = b""
            encrypted_key = b""
            ecdh_pub_key = None
            ecdh_sig = None
            client_version = 7

        data = ProtoHandler.big_payload(
            client_version=client_version,
            session_key=self._session.session_id,
            launch_spec=launch_spec,
            encrypted_key=encrypted_key,
            ecdh_pub_key=ecdh_pub_key,
            ecdh_sig=ecdh_sig,
        )
        log_bytes("Big Payload", data)
        self.send_data(data, chunk_flag, channel)

    def _format_launch_spec(self, handshake_key: bytes, format_type=None) -> bytes:
        launch_spec = get_launch_spec(
            handshake_key=handshake_key,
            resolution=self.resolution,
            max_fps=self.max_fps,
            rtt=int(self.rtt),
            mtu_in=self.mtu,
            quality=self._session.quality,
        )
        if format_type == "raw":
            return launch_spec

        launch_spec_enc = bytearray(len(launch_spec))
        launch_spec_enc = self._session._cipher.encrypt(launch_spec_enc, counter=0)

        if format_type == "encrypted":
            return launch_spec_enc
        launch_spec_xor = strxor(launch_spec_enc, launch_spec)
        if format_type == "xor":
            return launch_spec_xor

        launch_spec_b64 = base64.b64encode(launch_spec_xor)
        return launch_spec_b64

    def set_ciphers(self, ecdh_pub_key: bytes, ecdh_sig: bytes):
        """Set Ciphers."""
        if not self._ecdh.set_secret(ecdh_pub_key, ecdh_sig):
            self._stop_event.set()
        self.cipher = self._ecdh.init_ciphers()
        self.ready()
        if self.av_handler.has_receiver:
            self.av_handler.set_cipher(self.cipher)

    def _disconnect(self):
        """Disconnect Stream."""
        _LOGGER.info("Stream Disconnecting")
        chunk_flag = channel = 1
        data = ProtoHandler.disconnect_payload()
        self._advance_sequence()
        self.send_data(data, chunk_flag, channel)

    def stop(self):
        """Stop Stream."""
        _LOGGER.info("Stopping Stream")
        self._stop_event.set()
        if self._protocol:
            self._disconnect()
            self._protocol.close()
        if self._cb_stop is not None:
            self._cb_stop()

    def recv_stream_info(self, info: dict):
        """Receive stream info."""
        self.stream_info = info
        self.av_handler.set_headers(info["video_header"], info["audio_header"])

    def recv_bang(self, accepted: bool, ecdh_pub_key: bytes, ecdh_sig: bytes):
        """Receive Bang Payload."""
        if self._is_test and self._test:
            self.ready()
            self._test.run_rtt()
        else:
            if accepted:
                self.set_ciphers(ecdh_pub_key, ecdh_sig)
            else:
                _LOGGER.error("RP Launch Spec not accepted")
                self._stream._stop_event.set()

    def wait_for_ack(self, tsn: int, cb: callable):
        """Wait for ack received."""
        self._cb_ack = cb
        self._cb_ack_tsn = tsn

    @property
    def state(self) -> str:
        """Return State."""
        return self._state


class StreamTest():
    def __init__(self, stream: RPStream):
        self._stream = stream
        self._index = 0
        self._max_pings = 10
        self._ping_times = []
        self._mtu_test_in = True
        self._cur_mtu = 0
        self._last_mtu = 0
        self._mtu_in = 0
        self._results = {"rtt": DEFAULT_RTT, "mtu": DEFAULT_MTU}

    def _send_echo_command(self, enable: bool):
        chunk_flag = 1
        channel = 8
        data = ProtoHandler.senkusha_echo(enable)
        cb = self.send_rtt if enable else self.stop_rtt
        self._stream._advance_sequence()
        self._stream.wait_for_ack(self._stream._tsn, cb)
        self._stream.send_data(data, chunk_flag, channel)

    def _send_mtu_in(self):
        chunk_flag = 1
        channel = 8
        self._index += 1
        data = ProtoHandler.senkusha_mtu(self._index, self._cur_mtu, 1)
        self._stream._advance_sequence()
        self._stream.send_data(data, chunk_flag, channel)

    def _send_mtu_out(self):
        chunk_flag = 1
        channel = 8
        self._index += 1
        data = ProtoHandler.senkusha_mtu_client(True, self._index, self._mtu_in, self._mtu_in)
        self._stream._advance_sequence()
        self._stream.send_data(data, chunk_flag, channel)

    def _get_test_packet(self, length) -> bytes:
        index = 0x1f + (self._index * 0x20)
        gmac = int.from_bytes(get_random_bytes(4), "big")
        buf = bytearray(length)
        pack_into("!B", buf, 0, Header.Type.AUDIO)
        pack_into("!HBxB", buf, 5, index, 0xfc, 0xff)
        pack_into("!I", buf, 22, gmac)
        return bytes(buf)

    def stop(self):
        """Stop Tests."""
        self._stream.rtt = self._results["rtt"]
        self._stream.mtu = self._results["mtu"]
        _LOGGER.info("Tested network and got MTU: %s; RTT: %sms", self._results["mtu"], self._results["rtt"] * 1000)
        self._stream.stop()

    def run_rtt(self):
        """Start RTT Test."""
        _LOGGER.debug("Running RTT test...")
        self._send_echo_command(True)

    def send_rtt(self):
        """Send RTT Packet."""
        buf = self._get_test_packet(548)
        self._ping_times.insert(self._index, [time.time(), 0])
        self._stream.send(buf)

    def recv_rtt(self):
        """Receive RTT Packet."""
        _LOGGER.debug("Received RTT Echo")
        return_time = time.time()
        self._ping_times[self._index][1] = return_time
        self._index += 1

        if self._index < self._max_pings:
            self.send_rtt()
        else:
            self._send_echo_command(False)
            _LOGGER.debug("Stopping RTT Test")

    def stop_rtt(self):
        """Stop RTT Test."""
        _LOGGER.debug("RTT Test Complete")
        rtt_results = []
        for ping in self._ping_times:
            rtt_results.append(ping[1] - ping[0])
        average = sum(rtt_results) / self._max_pings
        longest = max(rtt_results)
        _LOGGER.info("Average RTT: %s ms; Longest RTT: %s ms", average, longest)
        self._results["rtt"] = average
        self.run_mtu_in()

    def run_mtu_in(self):
        """Run MTU In Test."""
        _LOGGER.info("Running MTU Test")
        self._index = 0
        self._cur_mtu = DEFAULT_MTU
        self._send_mtu_in()

    def stop_mtu_in(self):
        """Stop MTU In Test."""
        _LOGGER.debug("MTU IN Test Complete")
        _LOGGER.debug("MTU IN: %s", self._last_mtu)
        self._mtu_in = self._last_mtu
        self._results["mtu"] = self._mtu_in
        self.stop()

    def run_mtu_out(self):
        """Run MTU Out Test."""
        self._index = 0
        self._cur_mtu = DEFAULT_MTU
        self._send_mtu_out()

    def recv_mtu(self, msg: bytes):
        """Receive MTU."""
        log_bytes("Mtu", msg)
        # Add UDP header length
        mtu = len(msg) + UDP_IPV4_SIZE
        self._last_mtu = mtu

    def recv_mtu_in(self, mtu_req: int, mtu_sent: int):
        """Receive MTU Packet data."""
        if mtu_req != mtu_sent:
            _LOGGER.error("MTU requested %s but received %s", mtu_req, mtu_sent)
            self.stop_mtu_in()
        elif self._last_mtu:
            if self._last_mtu == mtu_sent:
                _LOGGER.debug("MTU at maximum: %s", self._last_mtu)
                self.stop_mtu_in()
            elif self._last_mtu < mtu_sent:
                _LOGGER.debug("MTU RECV %s less than sent %s", self._last_mtu, mtu_sent)
                self._cur_mtu -= (self._cur_mtu - self._last_mtu) // 2
                if self._index < 3:
                    self._last_mtu = 0
                    self._send_mtu_in()
                else:
                    self.stop_mtu_in()
            else:
                self.stop_mtu_in()
