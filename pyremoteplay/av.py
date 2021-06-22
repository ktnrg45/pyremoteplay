"""AV for pyremoteplay."""
import logging
import queue
import time

import numpy as np
from construct import (Bytes, BytesInteger, Const, GreedyBytes, Int32ub,
                       Padding, Struct)

from .util import from_b, log_bytes, to_b

_LOGGER = logging.getLogger(__name__)

HEADER_TYPE_VIDEO = 0x02
HEADER_TYPE_AUDIO = 0x03

def parse_av_packet_v9(msg, is_video: bool):
    """Parse V9 AV Packet."""
    data = {}
    has_nalu = False
    fmt = Struct(
        'base_type' / Bytes(1),
        'packet_index' / Bytes(2),
        'frame_index' / Bytes(2),
        'dword_2' / Bytes(4),
        'codec' / Bytes(1),
        'unknown' / Bytes(4),
        'key_pos' / Bytes(4),
        'data' / GreedyBytes,
    )
    packet = fmt.parse(msg)
    if (from_b(packet.base_type) >> 4) & 1 != 0:
        has_nalu = True

    if is_video:
        v_fmt = Struct(
            'word_0x18' / Bytes(2),
            'adaptive_stream_index' / Bytes(1),
            'data' / GreedyBytes,
        )
        v_data = v_fmt.parse(packet.data)
        data['adaptive_stream_index'] = from_b(
            v_data.adaptive_stream_index) >> 5
        if has_nalu:
            v_data.data = v_data.data[3:]
        data['has_nalu'] = has_nalu
        data['data'] = v_data.data

    # Audio
    else:
        data['data'] = packet.data[1:]

    d_type = 'video' if is_video else 'audio'
    data['type'] = d_type
    data['packet_index'] = from_b(packet.packet_index)
    data['frame_index'] = from_b(packet.frame_index)
    data['codec'] = from_b(packet.codec)
    data['key_pos'] = from_b(packet.key_pos)

    dword_2 = from_b(packet.dword_2)
    if is_video:
        data['unit_index'] = ((dword_2 >> 0x15) & 0x7ff)
        data['units_frame_total'] = (((dword_2 >> 0xa) & 0x7ff) + 1)
        data['units_frame_fec'] = (dword_2 & 0x3ff)

        source_units = data['units_frame_total'] - data['units_frame_fec']
        data['units_frame_src'] = source_units
        data['has_nalu'] = has_nalu

    else:
        data['unit_index'] = ((dword_2 >> 0x18) & 0xff)
        data['units_frame_total'] = (((dword_2 >> 0x10) & 0xff) + 1)
        fec = dword_2 & 0xffff
        data['unit_size_audio'] = fec >> 8
        data['units_source_audio'] = fec & 0xf
        data['units_fec_audio'] = (fec >> 4) & 0xf

    return data


class VideoFrame():
    def __init__(self, data: dict, callback):
        self.callback = callback
        self.total_size = data["units_frame_total"]
        self.src_size = data["units_frame_src"]
        self.fec_size = data["units_frame_fec"]
        self.src_packets = [0] * self.src_size
        self.fec_packets = [0] * self.fec_size
        self.src_count = 0
        self.fec_count = 0
        self.complete = False

        self.add_packet(data)


    def add_packet(self, data):
        index = data["unit_index"]
        if index + 1 <= self.src_size:
            self.src_packets[index] = data["data"]
            self.src_count += 1
        else:
            self.fec_packets[index - self.src_size] = data["data"]
            self.fec_count += 1
        if self.src_size == self.src_count and not self.complete:
            self.complete = True
            self.callback(b"".join(self.src_packets))


class AVReceiver():
    """AV Receiver."""
    def __init__(self, stream):
        self.stream = stream
        self._send_buf = queue.Queue()
        self._timer = None
        self.v_header = None
        self.a_header = None
        self.v_complete = 0
        self.a_complete = 0
        self.v_cur = -1
        self.a_cur = -1
        self.v_frame = None
        self.a_frame = None
        self.v_fec = None
        self.a_fec = None

    def set_headers(self, video: bytes, audio: bytes):
        """Set AV Headers."""
        self.v_header = video
        self.a_header = audio

    def handle_av(self, msg: bytes):
        is_video = from_b(msg[:1]) & 0x0F == HEADER_TYPE_VIDEO
        data = parse_av_packet_v9(msg, is_video)
        data["data"] = self.stream.cipher.decrypt(data["data"], data["key_pos"])
        if is_video:
            self.handle_video(data)
        else:
            self.handle_audio(data)

    def handle_video(self, data):
        if data['frame_index'] == self.v_cur:
            if self.v_frame is not None:
                self.v_frame.add_packet(data)
        else:
            if data['frame_index'] > self.v_cur + 1 and self.v_frame is not None and not self.v_frame.complete and self.v_cur >= 0:
                _LOGGER.error("Unfinished frame: %s Got frame: %s", self.v_cur, data['frame_index'])
            self.v_cur = data['frame_index']
            self._timer = time.time()
            self.v_frame = VideoFrame(data, self.handle_video_frame)

    def handle_audio(self, data):
        pass

    def handle_video_frame(self, frame):
        with open("test.h264", "ab") as f:
            f.write(frame)

        self.v_frame = None
        self.v_complete += 1
        self.v_cur += 1
        fps = 1/(time.time() - self._timer)
        _LOGGER.debug("Completed Video Frames %s/%s; FPS: %s", self.v_complete, self.v_cur, fps)
