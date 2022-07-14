Sessions
===============================================================================================

The :class:`Session <pyremoteplay.session.Session>` class is responsible for connecting to a Remote Play session.

It is recommended to create a `Session` using the :meth:`RPDevice.create_session() <pyremoteplay.device.RPDevice.create_session>`,
method instead of creating it directly.

A :class:`RPDevice <pyremoteplay.device.RPDevice>` instance can only have one `Session` instance coupled to it at a time. 

There are multiple parameters for creating a session which will configure options such as frame rate and
the resolution of the video stream.

Creating a Session
+++++++++++++++++++++++++++++++++++++++++++++

The following are parameters for :meth:`RPDevice.create_session() <pyremoteplay.device.RPDevice.create_session>`

The only required argument is `user`. The remaining arguments should be passed as **keyword arguments**.

.. list-table:: Parameters for :meth:`RPDevice.create_session() <pyremoteplay.device.RPDevice.create_session>`
   :widths: 25 10 15 50
   :header-rows: 1

   * - Parameter
     - Type
     - Default
     - Description

   * - **user**
     - :class:`str <str>`
     - <**required**>
     - | The username / PSN ID to connect with.
       | A list of users can be found with :meth:`RPDevice.get_users() <pyremoteplay.device.RPDevice.get_users>`.

   * - **profiles**
     - :class:`Profiles <pyremoteplay.profile.Profiles>`
     - `None`
     - A profiles object. Generally not needed as the :class:`RPDevice <pyremoteplay.device.RPDevice>` class will pass this to `Session`.

   * - **loop**
     - :class:`asyncio.AbstractEventLoop <asyncio.AbstractEventLoop>`
     - `None`
     - | The `asyncio` Event Loop to use. Must be running. Generally not needed.
       | If not specified, the currently running loop will be used.

   * - **receiver**
     - :class:`AVReceiver <pyremoteplay.receiver.AVReceiver>`
     - `None`
     - | The receiver to use.
       | **Note:** Must be a sub-class of AVReceiver; See :class:`QueueReceiver <pyremoteplay.receiver.QueueReceiver>`.
       | The receiver exposes audio and video frames from the live stream.
       | If not provided then no video/audio will be processed.

   * - **resolution**
     - :class:`Resolution <pyremoteplay.const.Resolution>` or :class:`str <str>` or :class:`int <int>`
     - `360p`
     - | The resolution to use for video stream.
       | Must be one of ["360p", "540p", "720p", "1080p"].

   * - **fps**
     - :class:`FPS <pyremoteplay.const.FPS>` or :class:`str <str>` or :class:`int <int>`
     - `low`
     - | The FPS / frame rate for the video stream.
       | Can be expressed as ["low", "high"] or [30, 60].

   * - **quality**
     - :class:`Quality <pyremoteplay.const.Quality>` or :class:`str <str>` or :class:`int <int>`
     - `very_low`
     - | The quality of the video stream. Represents the bitrate of the stream.
       | Must be a valid member of the `Quality` enum.
       | Using `DEFAULT` will use the appropriate bitrate for a specific resolution.

   * - **codec**
     - :class:`str <str>`
     - `h264`
     - | The `FFMPEG` video codec to use. Valid codecs start with either "h264" or "hevc".
       | There are several FFMPEG Hardware Decoding codecs that can be used such as "h264_cuvid".
       | On devices which do not support "hevc", "h264" will always be used.

   * - **hdr**
     - :class:`bool <bool>`
     - `False`
     - Whether HDR should be used for the video stream. This is only used with the "hevc" codec.

Connecting to a Session
+++++++++++++++++++++++++++++++++++++++++++++

To connect to a created session, use the async coroutine :meth:`RPDevice.connect() <pyremoteplay.device.RPDevice.connect>`.

After connecting, one should wait for it to be ready before using it.
This can be done with the :meth:`RPDevice.wait_for_session() <pyremoteplay.device.RPDevice.wait_for_session>` method or
the :meth:`RPDevice.async_wait_for_session() <pyremoteplay.device.RPDevice.async_wait_for_session>` coroutine.

The :meth:`RPDevice.ready <pyremoteplay.device.RPDevice.ready>` property will return True if the Session is ready.

Disconnecting from a Session
+++++++++++++++++++++++++++++++++++++++++++++

To disconnect, simply call the :meth:`RPDevice.disconnect() <pyremoteplay.device.RPDevice.disconnect>` method.

**Note:** This will also destroy the Session object and the :meth:`RPDevice.session <pyremoteplay.device.RPDevice.session>` property will be set to `None`.