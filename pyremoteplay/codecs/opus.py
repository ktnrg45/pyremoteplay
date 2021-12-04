"""Decoder for Opus Audio."""

import array
import ctypes

from pyogg import opus

SAMPLE_RATE = 48000
CHANNELS = 2


class OpusDecoder:
    """Decoder for Opus frames"""

    def __init__(self, sample_rate=SAMPLE_RATE, channels=CHANNELS):
        self.sample_rate = sample_rate
        self.channels = channels
        self._decoder = self._init_decoder()

    def _init_decoder(self):
        sample_rate = opus.opus_int32(self.sample_rate)
        channels = opus.opus_int32(self.channels)
        size = opus.opus_decoder_get_size(channels)
        memory = ctypes.create_string_buffer(size)
        decoder = ctypes.cast(memory, ctypes.POINTER(opus.OpusDecoder))

        error = opus.opus_decoder_init(decoder, sample_rate, channels)
        if error != opus.OPUS_OK:
            raise RuntimeError("Opus Decoder failed to init")
        return decoder

    def _get_pcm_buffer(self):
        max_duration = 120
        max_samples = max_duration * self.sample_rate // 1000
        buf = opus.opus_int16 * (max_samples * self.channels)
        buf = buf()
        buf_ptr = ctypes.cast(ctypes.pointer(buf), ctypes.POINTER(opus.opus_int16))
        buf_size = ctypes.c_int(max_samples)
        return (buf, buf_ptr, buf_size)

    def decode(self, data: bytes) -> bytes:
        """Return raw PCM bytes from Opus frames."""
        buf = ctypes.c_char * len(data)
        buf = buf.from_buffer(bytearray(data))
        buf_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        length = opus.opus_int32(len(data))

        (
            pcm,  # pylint: disable=unused-variable
            pcm_ptr,
            pcm_size,
        ) = self._get_pcm_buffer()

        samples = opus.opus_decode(
            self._decoder,
            buf_ptr,
            length,
            pcm_ptr,
            pcm_size,
            0,
        )
        return array.array("h", pcm_ptr[: samples * self.channels]).tobytes()
