Audio / Video Stream
===============================================================================================

The live audio/video stream is exposed through the :class:`AVReceiver <pyremoteplay.receiver.AVReceiver>` class.

The `AVReceiver` class **must** be **subclassed** and have implementations for the 
:meth:`AVReceiver.handle_video() <pyremoteplay.receiver.AVReceiver.handle_video>`
and
:meth:`AVReceiver.handle_audio() <pyremoteplay.receiver.AVReceiver.handle_audio>`
methods. The audio and video frames that are passed to these methods are `pyav <https://pyav.org/docs/stable/>`_ frames.

A generic receiver is provided in this library with the :class:`QueueReceiver <pyremoteplay.receiver.QueueReceiver>` class.

Usage
+++++++++++++++++++++++++++++++++++++++++++++
To use a receiver, the receiver must be passed as a keyword argument to the 
:meth:`RPDevice.create_session <pyremoteplay.RPDevice.create_session>`
method like in the example below.

::

   from pyremoteplay import RPDevice
   from pyremoteplay.receiver import QueueReceiver

   ip_address = "192.168.86.2"
   device = RPDevice(ip_address)
   device.get_status()
   user = device.get_users()[0]
   receiver = QueueReceiver()
   device.create_session(user, receiver=receiver)