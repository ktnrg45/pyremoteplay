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
    def __init__(self):
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

    # def handle_av(self, msg: bytes):
    #     is_video = from_b(msg[:1]) & 0x0F == HEADER_TYPE_VIDEO
    #     data = parse_av_packet_v9(msg, is_video)
    #     data["data"] = self.stream.cipher.decrypt(data["data"], data["key_pos"])
    #     if is_video:
    #         self.handle_video(data)
    #     else:
    #         self.handle_audio(data)

    # def handle_video(self, data):
    #     if data['frame_index'] == self.v_cur:
    #         if self.v_frame is not None:
    #             self.v_frame.add_packet(data)
    #     else:
    #         if data['frame_index'] > self.v_cur + 1 and self.v_frame is not None and not self.v_frame.complete and self.v_cur >= 0:
    #             _LOGGER.error("Unfinished frame: %s Got frame: %s", self.v_cur, data['frame_index'])
    #         self.v_cur = data['frame_index']
    #         self._timer = time.time()
    #         self.v_frame = VideoFrame(data, self.handle_video_frame)

    # def handle_audio(self, data):
    #     pass

    def handle_video(self, packet):
        with open("test.h264", "ab") as f:
            if packet.unit_index == 0:
                f.write(self.v_header)
            if packet.unit_index < packet.frame_meta['units']['src']:
                f.write(packet.data)

        # self.v_frame = None
        # self.v_complete += 1
        # self.v_cur += 1
        # fps = 1/(time.time() - self._timer)
        # _LOGGER.debug("Completed Video Frames %s/%s; FPS: %s", self.v_complete, self.v_cur, fps)
